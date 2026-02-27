import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import _audit_single_stock
import config

if __name__ == "__main__":
    print("Testing pipeline for 601318...")
    _audit_single_stock("601318", 1.5)
    print("Test finished.")

