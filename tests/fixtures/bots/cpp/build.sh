#!/bin/bash
set -euo pipefail

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j "${SCRIMMAGE_CORES:-1}"
