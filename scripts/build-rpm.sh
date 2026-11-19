#!/usr/bin/env bash
# Build Mooring's RPM from the working tree (tracked + new, not ignored files).
# Output: build/rpm/RPMS/noarch/mooring-<version>-*.rpm
set -euo pipefail
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
top="$PWD/build/rpm"
rm -rf "$top"
mkdir -p "$top"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
git ls-files -co --exclude-standard -z \
    | tar --null -T - --transform "s,^,mooring-$version/," -czf "$top/SOURCES/mooring-$version.tar.gz"
cp packaging/mooring.spec "$top/SPECS/"
rpmbuild --define "_topdir $top" -ba "$top/SPECS/mooring.spec"
find "$top/RPMS" "$top/SRPMS" -name '*.rpm' -printf '%p\n'
