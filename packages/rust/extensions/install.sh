#!/usr/bin/env bash
# GoldenMatch Postgres Extension Installer
# Usage: curl -sSL https://raw.githubusercontent.com/benzsevern/goldenmatch-extensions/main/install.sh | bash
set -euo pipefail

VERSION="${GOLDENMATCH_VERSION:-v0.1.0}"
PG_VERSION=""
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

echo "=== GoldenMatch Postgres Extension Installer ==="

# Detect pg_config
if ! command -v pg_config &>/dev/null; then
    echo "ERROR: pg_config not found. Is PostgreSQL installed?"
    echo "       On Ubuntu/Debian: sudo apt install postgresql-server-dev-16"
    echo "       On macOS: brew install postgresql@16"
    exit 1
fi

PG_VERSION=$(pg_config --version | grep -oP '\d+' | head -1)
LIBDIR=$(pg_config --pkglibdir)
SHAREDIR=$(pg_config --sharedir)/extension

echo "PostgreSQL: ${PG_VERSION}"
echo "Library dir: ${LIBDIR}"
echo "Extension dir: ${SHAREDIR}"

# Check Python + goldenmatch
if ! python3 -c "import goldenmatch; print(f'goldenmatch {goldenmatch.__version__}')" 2>/dev/null; then
    echo ""
    echo "WARNING: Python package 'goldenmatch' not found."
    echo "         Install with: pip install goldenmatch>=1.1.0"
    echo "         The extension will not work without it."
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Download binary
PKG_NAME="goldenmatch_pg-${VERSION}-pg${PG_VERSION}-py312-${OS}-${ARCH}"
URL="https://github.com/benzsevern/goldenmatch-extensions/releases/download/${VERSION}/${PKG_NAME}.tar.gz"

echo ""
echo "Downloading ${PKG_NAME}..."
TMPDIR=$(mktemp -d)
trap "rm -rf ${TMPDIR}" EXIT

if ! curl -sSL -o "${TMPDIR}/pkg.tar.gz" "${URL}"; then
    echo "ERROR: Download failed. Check version and platform:"
    echo "       URL: ${URL}"
    echo "       Available at: https://github.com/benzsevern/goldenmatch-extensions/releases"
    exit 1
fi

tar xzf "${TMPDIR}/pkg.tar.gz" -C "${TMPDIR}"

# Install
echo "Installing to ${LIBDIR} and ${SHAREDIR}..."
NEED_SUDO=""
if [ ! -w "${LIBDIR}" ]; then
    NEED_SUDO="sudo"
    echo "(requires sudo for file copy)"
fi

${NEED_SUDO} cp "${TMPDIR}/${PKG_NAME}/goldenmatch_pg.so" "${LIBDIR}/"
${NEED_SUDO} cp "${TMPDIR}/${PKG_NAME}/goldenmatch_pg.control" "${SHAREDIR}/"
${NEED_SUDO} cp "${TMPDIR}/${PKG_NAME}/goldenmatch_pg--0.1.0.sql" "${SHAREDIR}/"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Now connect to PostgreSQL and run:"
echo "  CREATE EXTENSION goldenmatch_pg;"
echo ""
echo "Quick test:"
echo "  SELECT goldenmatch.goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');"
echo ""
