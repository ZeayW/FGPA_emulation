#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 OPENPARF_INSTALL EXPORT_PATCH" >&2
  exit 2
fi

install_dir=$1
patch_file=$2
target=$install_dir/openparf/placement/placer.py

if [[ ! -f "$target" ]]; then
  echo "OpenPARF placer not found: $target" >&2
  exit 1
fi

patch --directory "$install_dir" --forward --strip 1 <"$patch_file"
python3 -m py_compile "$target"
echo "Enabled OpenPARF global-coordinate export in $install_dir"
