import os
from utils.dm_helpers import run_cmd

def test_data_is_obfuscated_on_backing_drives(dm_xor, tmp_path):
    """Test that data written to the dm-xor target is not stored in plaintext on the backing drives."""
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=10)
    
    # We will write a highly predictable pattern (e.g., all "A"s)
    # 0x41 is 'A'
    pattern_hex = "41"
    
    # Write 1MB of "A"s
    run_cmd(f"sudo awk 'BEGIN {{ for(i=0;i<1048576;i++) printf \"A\" }}' | sudo dd of={xor_dev} bs=1M count=1 status=none")
    
    # Verify the dm-xor device returns "A"s
    read_data = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 status=none | hexdump -n 16 -C")
    assert "41 41 41 41" in read_data, "Verification failed: dm-xor device did not return the expected plaintext."
    
    # Now read from the backing devices directly. They should NOT contain a block of "A"s.
    for dev in backing_devs:
        # We read the first 1MB of the backing device
        raw_data = run_cmd(f"sudo dd if={dev} bs=1M count=1 status=none | hexdump -n 256 -C")
        
        # We assume the dm-xor algorithm will either split data, encode it, or XOR it with something.
        # If the backing device has 41 41 41 41... then it failed to obfuscate.
        assert "41 41 41 41 41 41 41 41" not in raw_data, f"Data leakage detected! Backing drive {dev} contains plaintext data."

def test_xor_math(dm_xor, tmp_path):
    """
    Test that the XOR operation is actually happening correctly.
    Since we don't know the exact internal implementation details (e.g., if it uses parity or strict splitting),
    we can test a basic property: if we change underlying data, the read data changes.
    """
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=10)
    
    # Write a block
    run_cmd(f"sudo awk 'BEGIN {{ for(i=0;i<1048576;i++) printf \"X\" }}' | sudo dd of={xor_dev} bs=1M count=1 status=none")
    
    orig_md5 = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 status=none | md5sum | awk '{{print $1}}'")
    
    # Corrupt the first backing device slightly
    run_cmd(f"sudo bash -c 'echo -n \"CORRUPTED\" | dd of={backing_devs[0]} bs=1 count=9 conv=notrunc status=none'")
    
    # Read back from dm-xor device. It should now have a different checksum.
    new_md5 = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 status=none | md5sum | awk '{{print $1}}'")
    
    assert orig_md5 != new_md5, "Changing the backing drive did not change the read result of the dm-xor device."
