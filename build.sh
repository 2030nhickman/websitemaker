#!/usr/bin/env bash
# Build the SiteForge Debian package, or install it for the current user only.
#
#   ./build.sh                 build siteforge_1.0.0_amd64.deb (default)
#   ./build.sh user-install    install into ~/.local (no root needed)
#   ./build.sh user-uninstall  remove the ~/.local install
#   ./build.sh clean           remove build output
#
# Needs only coreutils and dpkg-deb; makes no network calls.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

NAME=siteforge
VERSION=$(python3 -c 'import re; print(re.search(r"__version__ = \"(.+?)\"", open("siteforge/__init__.py").read()).group(1))')
ARCH=amd64
APP_ID=org.siteforge.SiteForge
DEB="${NAME}_${VERSION}_${ARCH}.deb"
STAGE="build/${NAME}_${VERSION}_${ARCH}"

# Copy the files every install layout needs into $1 (a prefix such as /usr).
install_tree() {
    local root=$1 share=$1/share
    install -d "$share/$NAME" "$share/applications" "$share/icons/hicolor/256x256/apps" \
        "$share/metainfo" "$share/mime/packages" "$root/bin"
    # 1-2. the Python application (without caches) and example projects
    (tar --exclude='__pycache__' --exclude='*.pyc' -cf - siteforge examples) | tar -xf - -C "$share/$NAME"
    # 3. desktop entry, 4. icon, plus AppStream metadata and the .siteforge MIME type
    install -m 644 "data/$APP_ID.desktop" "$share/applications/$APP_ID.desktop"
    install -m 644 data/icons/hicolor/256x256/apps/siteforge.png "$share/icons/hicolor/256x256/apps/siteforge.png"
    install -m 644 "data/$APP_ID.metainfo.xml" "$share/metainfo/$APP_ID.metainfo.xml"
    install -m 644 data/siteforge-mime.xml "$share/mime/packages/siteforge.xml"
    # 5. launcher wrapper
    cat > "$root/bin/$NAME" <<EOF
#!/bin/sh
PYTHONPATH="$2/share/$NAME\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m siteforge "\$@"
EOF
    chmod 755 "$root/bin/$NAME"
}

build_deb() {
    rm -rf "$STAGE"
    install_tree "$STAGE/usr" /usr
    install -d "$STAGE/usr/share/doc/$NAME"
    install -m 644 README.md DESIGN.md "$STAGE/usr/share/doc/$NAME/"
    find "$STAGE" -type d -exec chmod 755 {} +
    find "$STAGE/usr/share" -type f -exec chmod 644 {} +

    install -d "$STAGE/DEBIAN"
    local size
    size=$(du -sk "$STAGE/usr" | cut -f1)
    sed -e "s/@VERSION@/$VERSION/" -e "s/@ARCH@/$ARCH/" -e "s/@SIZE@/$size/" \
        packaging/debian/control.in > "$STAGE/DEBIAN/control"
    install -m 755 packaging/debian/postinst packaging/debian/postrm "$STAGE/DEBIAN/"

    # 6. build the package
    dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
    echo "Built $DEB"
    dpkg-deb --info "$DEB" | sed -n 's/^ *\(Package\|Version\|Architecture\|Depends\):/  &/p'
}

user_install() {
    local prefix="${XDG_DATA_HOME:-$HOME/.local/share}"
    prefix=${prefix%/share}
    install_tree "$prefix" "$prefix"
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$prefix/share/applications" || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$prefix/share/icons/hicolor" 2>/dev/null || true
    command -v update-mime-database >/dev/null && update-mime-database "$prefix/share/mime" || true
    echo "Installed to $prefix (make sure $prefix/bin is on your PATH)"
}

user_uninstall() {
    local prefix="${XDG_DATA_HOME:-$HOME/.local/share}"
    prefix=${prefix%/share}
    rm -rf "$prefix/share/$NAME"
    rm -f "$prefix/bin/$NAME" "$prefix/share/applications/$APP_ID.desktop" \
        "$prefix/share/icons/hicolor/256x256/apps/siteforge.png" \
        "$prefix/share/metainfo/$APP_ID.metainfo.xml" "$prefix/share/mime/packages/siteforge.xml"
    echo "Removed SiteForge from $prefix"
}

case "${1:-deb}" in
    deb) build_deb ;;
    user-install) user_install ;;
    user-uninstall) user_uninstall ;;
    clean) rm -rf build "${NAME}"_*.deb ;;
    *) echo "usage: $0 [deb|user-install|user-uninstall|clean]" >&2; exit 2 ;;
esac
