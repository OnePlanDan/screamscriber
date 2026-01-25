#!/usr/bin/env python3
"""
Test script to verify that the X11 connection leak in PynputBackend has been fixed.
This script will repeatedly start and stop the PynputBackend to simulate the issue
that occurs during normal application usage.
"""
import sys
import time
import os

# Add src directory to path
sys.path.insert(0, 'src')

from key_listener import PynputBackend

def test_x11_leak_fix():
    """Test that PynputBackend doesn't leak X11 connections when started/stopped repeatedly."""
    print("Testing X11 connection leak fix...")
    print("This will start and stop the PynputBackend 50 times.")
    print("If the fix works, this should complete without 'Maximum number of clients reached' error.")
    print()
    
    backend = PynputBackend()
    
    for i in range(50):
        try:
            print(f"Iteration {i+1}/50: Starting backend...")
            backend.start()
            time.sleep(0.1)  # Brief delay
            
            print(f"Iteration {i+1}/50: Stopping backend...")
            backend.stop()
            time.sleep(0.1)  # Brief delay
            
            if (i + 1) % 10 == 0:
                print(f"Completed {i+1} iterations successfully")
                
        except Exception as e:
            print(f"ERROR on iteration {i+1}: {e}")
            return False
    
    print("\n✅ SUCCESS: All 50 iterations completed without X11 connection leak!")
    print("The fix appears to be working correctly.")
    return True

if __name__ == "__main__":
    if not PynputBackend.is_available():
        print("❌ ERROR: PynputBackend is not available on this system")
        sys.exit(1)
        
    success = test_x11_leak_fix()
    sys.exit(0 if success else 1) 