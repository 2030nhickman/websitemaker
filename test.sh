#!/usr/bin/env bash
# SiteForge test suite.
#
#   ./test.sh              unit tests, GUI tests under xvfb, .deb build, clean-container install
#   ./test.sh --no-docker  skip the Debian 13 container install test
#
# Requirements: python3 with the runtime dependencies (see README), xvfb + xauth,
# dpkg-deb, and Docker (or Podman) for the clean-install test.
set -uo pipefail

cd "$(dirname "$(readlink -f "$0")")"
PY=${PYTHON:-python3}
USE_DOCKER=1
[ "${1:-}" = "--no-docker" ] && USE_DOCKER=0
FAILED=0
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
pass() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
skip() { printf '\033[33mSKIP\033[0m %s\n' "$*"; }

XVFB=(xvfb-run -a -s "-screen 0 1400x900x24")
# Containers rarely allow WebKit's bubblewrap sandbox; the preview only shows local pages.
export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=${WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS:-1}
export NO_AT_BRIDGE=1

step "Unit tests"
if $PY -m unittest discover -s tests -p 'test_*.py'; then pass "unit tests"; else fail "unit tests"; fi

step "Command line export"
OUT=$(mktemp -d)
if $PY -m siteforge --export "$OUT/site" --split examples/stripe-checkout.siteforge >/dev/null \
    && [ -f "$OUT/site/index.html" ] && [ -f "$OUT/site/script.js" ] \
    && ! grep -q data-sf-id "$OUT/site/index.html"; then
    pass "headless export"
else
    fail "headless export"
fi
rm -rf "$OUT"

if command -v xvfb-run >/dev/null; then
    step "Launch the app in a virtual display"
    LOG=$(mktemp)
    if timeout 120 "${XVFB[@]}" $PY -m siteforge --smoke-test examples/simple-page.siteforge >"$LOG" 2>&1 \
        && grep -q SITEFORGE_WINDOW_OK "$LOG"; then
        pass "window appears: $(grep SITEFORGE_WINDOW_OK "$LOG")"
    else
        fail "window did not appear"; tail -20 "$LOG"
    fi
    rm -f "$LOG"

    step "GUI interaction tests"
    if timeout 300 "${XVFB[@]}" $PY tests/gui_test.py 2>/dev/null; then
        pass "GUI tests"
    else
        fail "GUI tests"
    fi
else
    skip "xvfb-run not installed (apt install xvfb xauth)"
fi

step "Build the .deb"
if ./build.sh >/dev/null && [ -f siteforge_1.0.0_amd64.deb ]; then
    pass "built siteforge_1.0.0_amd64.deb"
    dpkg-deb --field siteforge_1.0.0_amd64.deb Depends
else
    fail "build.sh"
fi

if [ "$USE_DOCKER" = 1 ]; then
    step "Install on a clean Debian 13 (trixie) container"
    ENGINE=$(command -v docker || command -v podman || true)
    if [ -z "$ENGINE" ] || ! "$ENGINE" info >/dev/null 2>&1; then
        skip "no running Docker/Podman (use --no-docker to silence)"
    else
        # Installs the package as root, then launches it as an unprivileged user.
        if "$ENGINE" run --rm -v "$PWD/siteforge_1.0.0_amd64.deb:/tmp/siteforge.deb:ro" \
            -e WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 -e NO_AT_BRIDGE=1 \
            debian:trixie bash -euc '
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -qq
                apt-get install -y -qq /tmp/siteforge.deb xvfb xauth >/dev/null
                dpkg -s siteforge | grep -q "Status: install ok installed"
                test -f /usr/share/applications/org.siteforge.SiteForge.desktop
                test -f /usr/share/icons/hicolor/256x256/apps/siteforge.png
                siteforge --version
                useradd -m tester
                su tester -c "cd ~ && siteforge --export out /usr/share/siteforge/examples/block-animation.siteforge"
                su tester -c "cd ~ && timeout 120 xvfb-run -a -s \"-screen 0 1400x900x24\" \
                    siteforge --smoke-test /usr/share/siteforge/examples/stripe-checkout.siteforge 2>/dev/null" \
                    | grep SITEFORGE_WINDOW_OK
                apt-get remove -y -qq siteforge >/dev/null
                ! test -e /usr/bin/siteforge
            '; then
            pass "package installs, runs as a normal user, and removes cleanly on Debian 13"
        else
            fail "clean container install"
        fi
    fi
fi

echo
if [ "$FAILED" = 0 ]; then echo "All tests passed."; else echo "Some tests FAILED."; fi
exit $FAILED
