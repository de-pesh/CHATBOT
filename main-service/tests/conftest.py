import sys
import os

# Add the project root directory to the Python path
# This allows tests to find the 'app' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
