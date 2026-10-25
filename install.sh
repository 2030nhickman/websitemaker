#!/usr/bin/env bash
# Install SiteForge on Debian 13 (or another apt-based system).
#
#   ./install.sh            download the .deb (unless already here), install any
#                           missing dependencies, then install SiteForge
#   ./install.sh --force    reinstall even if this version is already installed
#   ./install.sh --remove   uninstall SiteForge
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/2030nhickman/websitemaker/main/install.sh | bash
#
# Environment overrides:
#   SITEFORGE_REF=<branch|tag>   where to download from (default: main)
#   SITEFORGE_DEB_URL=<url>      download the .deb from this exact URL
set -euo pipefail

VERSION=1.0.0
DEB="siteforge_${VERSION}_amd64.deb"
REPO="2030nhickman/websitemaker"
REF="${SITEFORGE_REF:-main}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/siteforge"
DEPENDS=(python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-webkit-6.0
         gir1.2-adw-1 gir1.2-gtksource-5 python3-stripe)

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

FORCE=0
case "${1:-}" in
    --force) FORCE=1 ;;
    --remove|--uninstall) ACTION=remove ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    "") ;;
    *) die "unknown option: $1 (try --help)" ;;
esac

command -v apt-get >/dev/null || die "this installer needs an apt-based system (Debian 13 recommended)"
command -v dpkg-query >/dev/null || die "dpkg-query not found"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
elif command -v sudo >/dev/null; then
    SUDO=(sudo)
else
    die "run as root or install sudo"
fi

if [ "${ACTION:-}" = remove ]; then
    say "Removing SiteForge"
    "${SUDO[@]}" apt-get remove -y siteforge
    exit 0
fi

arch=$(dpkg --print-architecture)
[ "$arch" = amd64 ] || say "warning: package is built for amd64, this system is $arch (it is pure Python, so it usually works)"

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }

# ---- 1. already installed? -------------------------------------------------
if [ "$FORCE" = 0 ] && installed siteforge \
    && [ "$(dpkg-query -W -f='${Version}' siteforge)" = "$VERSION" ]; then
    say "SiteForge $VERSION is already installed (use --force to reinstall). Run: siteforge"
    exit 0
fi

# ---- 2. find or download the .deb -----------------------------------------
script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi

deb_path=""
for candidate in "${script_dir:+$script_dir/dist/$DEB}" "${script_dir:+$script_dir/$DEB}" \
                 "$PWD/$DEB" "$PWD/dist/$DEB" "$CACHE/$DEB"; do
    if [ -n "$candidate" ] && [ -s "$candidate" ]; then
        deb_path=$candidate
        break
    fi
done

fetch() {  # fetch URL FILE
    if command -v curl >/dev/null; then
        curl -fL --retry 3 --progress-bar -o "$2" "$1"
    elif command -v wget >/dev/null; then
        wget -q --show-progress -O "$2" "$1"
    else
        return 127
    fi
}

if [ -n "$deb_path" ]; then
    say "Using existing package: $deb_path"
else
    if ! command -v curl >/dev/null && ! command -v wget >/dev/null; then
        say "Installing curl to download SiteForge"
        "${SUDO[@]}" apt-get update -qq
        "${SUDO[@]}" apt-get install -y -qq curl ca-certificates
    fi
    mkdir -p "$CACHE"
    base="https://raw.githubusercontent.com/$REPO/$REF/dist"
    url="${SITEFORGE_DEB_URL:-$base/$DEB}"
    say "Downloading $DEB"
    tmp="$CACHE/$DEB.part"
    fetch "$url" "$tmp" || { rm -f "$tmp"; die "download failed: $url"; }
    # Verify the checksum published next to the package (skipped for custom URLs).
    if [ -z "${SITEFORGE_DEB_URL:-}" ]; then
        if fetch "$base/$DEB.sha256" "$CACHE/$DEB.sha256" 2>/dev/null; then
            expected=$(cut -d' ' -f1 "$CACHE/$DEB.sha256")
            actual=$(sha256sum "$tmp" | cut -d' ' -f1)
            [ "$expected" = "$actual" ] || { rm -f "$tmp"; die "checksum mismatch for $DEB"; }
            say "Checksum OK"
        else
            say "warning: no checksum file found, skipping verification"
        fi
    fi
    mv "$tmp" "$CACHE/$DEB"
    deb_path="$CACHE/$DEB"
fi

dpkg-deb --field "$deb_path" Package 2>/dev/null | grep -qx siteforge \
    || die "$deb_path is not a valid SiteForge package (delete it and re-run)"

# ---- 3. dependencies --------------------------------------------------------
missing=()
for pkg in "${DEPENDS[@]}"; do
    installed "$pkg" || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
    say "Installing missing dependencies: ${missing[*]}"
    "${SUDO[@]}" apt-get update -qq
    "${SUDO[@]}" apt-get install -y "${missing[@]}"
else
    say "All dependencies are already installed"
fi

# ---- 4. SiteForge itself ----------------------------------------------------
say "Installing SiteForge $VERSION"
# apt needs a path it can read as the _apt user; copy out of private dirs first.
staged=$(mktemp -d /tmp/siteforge-install.XXXXXX)
cp "$deb_path" "$staged/$DEB"
chmod 755 "$staged"
chmod 644 "$staged/$DEB"
"${SUDO[@]}" apt-get install -y --reinstall "$staged/$DEB"
rm -rf "$staged"

say "Done. Launch SiteForge from the Activities overview or run: siteforge"
say "Example projects: /usr/share/siteforge/examples/"
