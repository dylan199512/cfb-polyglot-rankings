# 2026 Polyglot College Football Computer Poll

A 3-language college football ranking engine combining:
- **GnuCOBOL (`elo_engine.cob`)**: Fixed-point (`COMP-3`) Elo rating ledger with 3-tier conference baselines (`P4=1560`, `G6=1440`, `FCS=1200`) and a `1.25x` November K-factor boost.
- **Fortran (`colley_solver.f90`)**: 64-bit double-precision Gaussian elimination solver computing both Colley Matrix and Massey point-differential ratings.
- **Python (`pipeline2.py`)**: Orchestrates live CFBD & ESPN data, blends the 3-model BCS composite, projects the 12-team CFP bracket, and generates `dashboard.html`.

## Build & Run

    cobc -x -o elo_engine elo_engine.cob
    export SDKROOT=$(xcrun --show-sdk-path)
    gfortran -O2 -L"$SDKROOT/usr/lib" -o colley_solver colley_solver.f90
    export CFBD_API_KEY="your_api_key"
    python3 pipeline2.py
