#!/bin/sh
# fdebrus plate 180C56 (generic layout, records key 1/3) through 0.57.0 -> 0.57.1 -> 0.57.2
D=$(dirname "$0"); PY=${PY:-python3}; LIB=${LIB:?set LIB to a nikobus-connect checkout at 0.57.2}
S=$D/p1_store.json; rm -f $S
echo "== 0.57.0"; PYTHONPATH=$D/v0570 $PY -I -c "import sys;sys.path.insert(0,'$D/v0570');sys.argv=['x','$S','inventory','180C56','04'];exec(open('$D/p1_step.py').read())"
PYTHONPATH= $PY -I -c "import sys;sys.path.insert(0,'$D/v0570');sys.argv=['x','$S','links','180C56','04','1','3'];exec(open('$D/p1_step.py').read())"
echo "== 0.57.1 inventory"; $PY -I -c "import sys;sys.path.insert(0,'$D/v0571');sys.argv=['x','$S','inventory','180C56','04'];exec(open('$D/p1_step.py').read())"
echo "== 0.57.2 inventory"; $PY -I -c "import sys;sys.path.insert(0,'$LIB');sys.argv=['x','$S','inventory','180C56','04'];exec(open('$D/p1_step.py').read())"
echo "== 0.57.2 second inventory"; $PY -I -c "import sys;sys.path.insert(0,'$LIB');sys.argv=['x','$S','inventory','180C56','04'];exec(open('$D/p1_step.py').read())"
$PY -I -c "import sys;sys.path.insert(0,'$LIB');import nikobus_connect;print(nikobus_connect.__file__)"
