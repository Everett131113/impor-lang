# -*- coding: utf-8 -*-
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from impor.interpreter import parse_source
from impor.ctrans import c_transpile
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cimport_demo', 'main.impor'), encoding='utf-8').read()
ast = parse_source(src)
try:
    print(c_transpile(ast, source_name=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cimport_demo', 'main.impor')))
except Exception:
    traceback.print_exc()
