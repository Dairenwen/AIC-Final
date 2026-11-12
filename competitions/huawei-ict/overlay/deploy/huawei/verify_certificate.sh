#!/usr/bin/env bash
# Verify the server certificate and any bundled intermediates against the
# machine's OpenSSL trust store. Never contact a CA or disclose the key.
set -euo pipefail
[[ $# -eq 1 && -r "$1" ]] || { echo '缺少可读的证书链文件。' >&2; exit 2; }
openssl verify -purpose sslserver -untrusted "$1" "$1" >/dev/null 2>&1 || {
  echo '证书链未被系统信任、不完整或不允许用于 HTTPS 服务器；请检查证书链及系统 CA。' >&2
  exit 1
}
