#!/bin/bash
# Resources are sealed by the native app's Developer ID signature.
set -euo pipefail
wheel="$1"
wheel_digest="$2"
version="$3"
UV_VERSION="0.12.21"
runtime="${MED_LIT_RUNTIME_DIR:-$HOME/Library/Application Support/med-lit-mcp/runtime}"
temporary=""
cleanup() { if [[ -n "$temporary" ]]; then rm -rf "$temporary"; fi; }
trap cleanup EXIT
phase() { printf 'MED_LIT_PHASE:%s\n' "$1"; }
case "$(uname -m)" in
  arm64) target=aarch64-apple-darwin; digest=b88bda573e566ef9bced66b155fe0408626fbbc053aee1c30ba686f0728c9447 ;;
  x86_64) target=x86_64-apple-darwin; digest=2b336763b396ec6afa20c5a8b083538ca7402445b868311979d740a4344c17d8 ;;
  *) echo 'This Mac processor is not supported.' >&2; exit 1 ;;
esac
if [[ "$(uname -s)" != Darwin ]]; then echo 'This installer requires macOS.' >&2; exit 1; fi
phase runtime
uv_bin=""
if [[ "${MED_LIT_INSTALLER_FORCE_RUNTIME:-0}" != 1 ]]; then
  if command -v uv >/dev/null 2>&1; then uv_bin="$(command -v uv)";
  elif [[ -x /opt/homebrew/bin/uv ]]; then uv_bin=/opt/homebrew/bin/uv;
  elif [[ -x /usr/local/bin/uv ]]; then uv_bin=/usr/local/bin/uv; fi
fi
if [[ -z "$uv_bin" && -x "$runtime/uv" ]]; then uv_bin="$runtime/uv"; fi
if [[ -z "$uv_bin" ]]; then
  mkdir -p "$runtime"
  chmod 700 "$runtime"
  temporary="$(mktemp -d "$runtime/.download.XXXXXX")"
  archive="$temporary/uv.tar.gz"
  echo 'Downloading the verified med-lit runtime…'
  curl --fail --location --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 15 --max-time 180 \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz" --output "$archive"
  actual="$(shasum -a 256 "$archive")"
  if [[ "${actual%% *}" != "$digest" ]]; then
    echo 'Runtime checksum did not match. No downloaded program was installed.' >&2; exit 1
  fi
  tar -xzf "$archive" -C "$temporary"
  install -m 700 "$temporary/uv-$target/uv" "$runtime/uv"
  uv_bin="$runtime/uv"
  rm -rf "$temporary"
  temporary=""
fi
# Registration must outlive the downloaded app, its mounted DMG and translocation.
phase package
actual="$(shasum -a 256 "$wheel")"
if [[ "${actual%% *}" != "$wheel_digest" ]]; then echo 'The bundled med-lit package is damaged. Download the installer again.' >&2; exit 1; fi
package_dir="$runtime/packages/$version/$wheel_digest"
mkdir -p "$package_dir"
chmod 700 "$runtime" "$runtime/packages" "$runtime/packages/$version" "$package_dir"
persistent_wheel="$package_dir/$(basename "$wheel")"
stored_digest=""
if [[ -f "$persistent_wheel" ]]; then actual="$(shasum -a 256 "$persistent_wheel")"; stored_digest="${actual%% *}"; fi
if [[ "$stored_digest" != "$wheel_digest" ]]; then
  temporary="$(mktemp -d "$package_dir/.copy.XXXXXX")"
  install -m 600 "$wheel" "$temporary/$(basename "$wheel")"
  mv "$temporary/$(basename "$wheel")" "$persistent_wheel"
  rm -rf "$temporary"
  temporary=""
fi
phase settings
echo 'Preparing Python and med-lit. Complete the settings form when your browser opens.'
# exec keeps the native launcher's process group responsible for the full runtime.
exec "$uv_bin" tool run --python 3.11 --from "$persistent_wheel" \
  med-lit-mcp setup --ui --client chatgpt --launcher "$uv_bin" --package "$persistent_wheel"
