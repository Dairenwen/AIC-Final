#!/usr/bin/env python3
"""Check local Safetensors shards without loading a model or contacting a service."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


ITEM_BYTES = {
    'BOOL': 1, 'U8': 1, 'I8': 1, 'F8_E4M3': 1, 'F8_E5M2': 1,
    'I16': 2, 'U16': 2, 'F16': 2, 'BF16': 2,
    'I32': 4, 'U32': 4, 'F32': 4,
    'I64': 8, 'U64': 8, 'F64': 8,
}


def verify(directory: Path) -> dict:
    directory = directory.resolve()
    report = {'status': 'blocked', 'directory': str(directory), 'errors': [],
              'shards': [], 'metadata_sha256': {},
              'limits': 'Checks file structure and lengths; no payload reference-hash comparison, model loading or inference.'}
    try:
        for name in ('config.json', 'generation_config.json', 'tokenizer.json',
                     'tokenizer_config.json', 'chat_template.jinja', 'model.safetensors.index.json'):
            path = directory / name
            if not path.is_file() or not path.stat().st_size:
                raise ValueError(f'Missing or empty metadata: {name}')
            report['metadata_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        config = json.loads((directory / 'config.json').read_text())
        index = json.loads((directory / 'model.safetensors.index.json').read_text())
        weight_map = index['weight_map']
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError('Empty or invalid weight_map')
        report.update(model_type=config.get('model_type'), architectures=config.get('architectures'),
                      dtype=config.get('dtype', config.get('torch_dtype')))
        found = set()
        payload_bytes = parameter_count = 0
        for name in sorted(set(weight_map.values())):
            # A snapshot must not direct the reader outside its model directory.
            if not isinstance(name, str) or Path(name).name != name or not name.endswith('.safetensors'):
                raise ValueError('Invalid shard filename in index')
            path = directory / name
            size = path.stat().st_size
            with path.open('rb') as stream:
                raw = stream.read(8)
                if len(raw) != 8:
                    raise ValueError(f'Truncated shard: {name}')
                header_size = int.from_bytes(raw, 'little')
                if not 0 < header_size <= min(64 * 1024 * 1024, size - 8):
                    raise ValueError(f'Invalid Safetensors header: {name}')
                header = json.loads(stream.read(header_size))
            payload_size = size - 8 - header_size
            spans = []
            shard_count = 0
            for tensor, entry in header.items():
                if tensor == '__metadata__':
                    continue
                if tensor in found or weight_map.get(tensor) != name:
                    raise ValueError(f'Tensor index mismatch: {tensor}')
                shape = entry['shape']
                offsets = entry['data_offsets']
                if not isinstance(shape, list) or any(type(n) is not int or n < 0 for n in shape):
                    raise ValueError(f'Invalid tensor shape: {tensor}')
                if len(offsets) != 2 or any(type(n) is not int for n in offsets):
                    raise ValueError(f'Invalid tensor offsets: {tensor}')
                start, end = offsets
                if not 0 <= start <= end <= payload_size:
                    raise ValueError(f'Truncated tensor payload: {tensor}')
                elements = math.prod(shape)
                if end - start != elements * ITEM_BYTES[entry['dtype']]:
                    raise ValueError(f'Tensor byte-length mismatch: {tensor}')
                spans.append((start, end))
                found.add(tensor)
                shard_count += 1
                parameter_count += elements
            cursor = 0
            for start, end in sorted(spans):
                if start != cursor:
                    raise ValueError(f'Overlapping or discontinuous payload: {name}')
                cursor = end
            if cursor != payload_size:
                raise ValueError(f'Unexpected shard length: {name}')
            payload_bytes += payload_size
            report['shards'].append({'name': name, 'bytes': size, 'tensors': shard_count})
        if found != set(weight_map):
            raise ValueError('Missing tensors referenced by index')
        metadata = index.get('metadata', {})
        if metadata.get('total_size') != payload_bytes:
            raise ValueError('Total payload bytes differ from index metadata')
        if 'total_parameters' in metadata and metadata['total_parameters'] != parameter_count:
            raise ValueError('Total parameters differ from index metadata')
        report.update(status='files_verified', tensor_count=len(found), parameter_count=parameter_count,
                      payload_bytes=payload_bytes, total_file_bytes=sum(s['bytes'] for s in report['shards']))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Actual snapshot directory containing config.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = verify(args.directory)
    output = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output, end='')
    return 0 if report['status'] == 'files_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
