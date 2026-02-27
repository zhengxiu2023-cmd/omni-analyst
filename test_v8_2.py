from main import _audit_single_stock
from utils.logger import setup_logging

setup_logging()

# Provide a mock market volume of 1.5 trillion
_audit_single_stock("601318", 1.5)
