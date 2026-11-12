import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    'production_resources', Path(__file__).resolve().parents[2] / 'deploy/huawei/production_resources.py'
)
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


def test_resource_reporting_warns_but_does_not_claim_a_failure():
    messages = resources.describe('x86_64', 4 * resources.GIB, 15 * resources.GIB)
    assert any('内存低于 8 GiB' in line for line in messages)
    assert any('空间低于 20 GiB' in line for line in messages)


def test_unknown_resources_and_unverified_arch_are_explicit():
    messages = resources.describe('aarch64', None, None)
    assert any('仅经过 x86_64' in line for line in messages)
    assert any('无法读取当前可用内存' in line for line in messages)
    assert any('无法读取 Docker 存储' in line for line in messages)


def test_production_compose_does_not_interpolate_optional_build_flags():
    text = (Path(__file__).resolve().parents[2] / 'deploy/huawei/docker-production.yml').read_text()
    assert 'INSTALL_FORMULA_RUNTIME: "true"' in text
    assert 'INSTALL_PPT_RUNTIME: "true"' in text
