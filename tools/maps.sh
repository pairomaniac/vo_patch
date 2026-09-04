#!/bin/sh
# Build the retail->other-build maps and the resolution patch's port
# tables, into maps/.
#
#     sh tools/maps.sh RETAIL.exe JPRE.exe OEM.exe [JP.exe]
#
# Writes maps/jpre.pkl, maps/oem.pkl and, given the Japanese original,
# maps/jp.pkl (vomap.py, about a minute each) and a *_port.txt beside
# each (hiresport.py). A port table that fails generation is kept as its
# FAIL list: that list is the work left for that build. Needs python3-capstone (tools/setup-dev.sh checks).
# maps/ is ignored by git; the executables are not in the repository and
# neither is anything derived from them.
set -e
cd "$(dirname "$0")/.."
if [ $# -lt 3 ] || [ $# -gt 4 ]; then
    echo "usage: sh tools/maps.sh RETAIL.exe JPRE.exe OEM.exe [JP.exe]" >&2
    exit 2
fi
retail=$1
mkdir -p maps
builds="jpre oem"
[ $# -eq 4 ] && builds="$builds jp"
for build in $builds; do
    case $build in jpre) other=$2;; oem) other=$3;; jp) other=$4;; esac
    python3 tools/vomap.py "$retail" "$other" "maps/$build.pkl"
    if python3 tools/hiresport.py "maps/$build.pkl" > "maps/${build}_port.txt"; then
        echo "$build: port table generated - byte-verified only, see docs/HIRES.md before shipping it"
    else
        echo "$build: $(grep -c FAIL "maps/${build}_port.txt") unresolved; see maps/${build}_port.txt"
    fi
done
