#!/bin/sh
# Build the retail->other-build maps and the resolution patch's port
# tables, into maps/.
#
#     sh tools/maps.sh RETAIL.exe JAPAN.exe OEM.exe
#
# Writes maps/jp.pkl and maps/oem.pkl (vomap.py, about a minute each) and
# maps/jp_port.txt, maps/oem_port.txt (hiresport.py). A port table that
# fails generation is kept as its FAIL list: that list is the work left
# for that build. Needs python3-capstone (tools/setup-dev.sh checks).
# maps/ is ignored by git; the executables are not in the repository and
# neither is anything derived from them.
set -e
cd "$(dirname "$0")/.."
if [ $# -ne 3 ]; then
    echo "usage: sh tools/maps.sh RETAIL.exe JAPAN.exe OEM.exe" >&2
    exit 2
fi
retail=$1
mkdir -p maps
for build in jp oem; do
    if [ "$build" = jp ]; then other=$2; else other=$3; fi
    python3 tools/vomap.py "$retail" "$other" "maps/$build.pkl"
    if python3 tools/hiresport.py "maps/$build.pkl" > "maps/${build}_port.txt"; then
        echo "$build: port table generated - byte-verified only, see docs/HIRES.md before shipping it"
    else
        echo "$build: $(grep -c FAIL "maps/${build}_port.txt") unresolved; see maps/${build}_port.txt"
    fi
done
