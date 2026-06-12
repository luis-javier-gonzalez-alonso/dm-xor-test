import os
from utils.dm_helpers import run_cmd

def test_data_is_obfuscated_on_backing_drives(dm_xor, tmp_path):
    """Test that data written to the dm-xor target is not stored in plaintext on the backing drives."""
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=10)
    
    # We will write a highly predictable pattern (e.g., all "A"s)
    # 0x41 is 'A'
    pattern_hex = "41"
    
    # Write 1MB of "A"s
    run_cmd(f"sudo awk 'BEGIN {{ for(i=0;i<1048576;i++) printf \"A\" }}' | sudo dd of={xor_dev} bs=1M count=1 oflag=direct status=none")
    
    # Flush and drop caches to ensure we read from disk
    run_cmd("sudo sync && sudo sysctl -w vm.drop_caches=3", check=False)
    
    # Verify the dm-xor device returns "A"s
    read_data = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 iflag=direct status=none | hexdump -n 16 -C")
    assert "41 41 41 41" in read_data, "Verification failed: dm-xor device did not return the expected plaintext."
    
    # Now read from the backing devices directly. They should NOT contain a block of "A"s.
    for dev in backing_devs:
        # We read the first 1MB of the backing device
        raw_data = run_cmd(f"sudo dd if={dev} bs=1M count=1 iflag=direct status=none | hexdump -n 256 -C")
        
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
    run_cmd(f"sudo awk 'BEGIN {{ for(i=0;i<1048576;i++) printf \"X\" }}' | sudo dd of={xor_dev} bs=1M count=1 oflag=direct status=none")
    
    run_cmd("sudo sync && sudo sysctl -w vm.drop_caches=3", check=False)
    orig_md5 = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 iflag=direct status=none | md5sum | awk '{{print $1}}'")
    
    # Corrupt the first backing device slightly
    run_cmd(f"sudo bash -c 'echo -n \"CORRUPTED\" | dd of={backing_devs[0]} bs=1 count=9 conv=notrunc,fsync status=none'")
    
    run_cmd("sudo sync && sudo sysctl -w vm.drop_caches=3", check=False)
    # Read back from dm-xor device. It should now have a different checksum.
    new_md5 = run_cmd(f"sudo dd if={xor_dev} bs=1M count=1 iflag=direct status=none | md5sum | awk '{{print $1}}'")
    
    assert orig_md5 != new_md5, "Changing the backing drive did not change the read result of the dm-xor device."

def test_manual_xor_matches_virtual_device(dm_xor, tmp_path):
    """
    Test that applying bitwise XOR at the byte level between all backing files
    provides the exact same content as reading from the virtual device.
    """
    dev_count = 3
    xor_dev, backing_devs = dm_xor(dev_count=dev_count, size_mb=10)
    
    # Write some random data to the virtual device
    test_data = os.urandom(1024 * 1024) # 1 MB
    test_file = tmp_path / "test_data.bin"
    with open(test_file, "wb") as f:
        f.write(test_data)
        
    run_cmd(f"sudo dd if={test_file} of={xor_dev} bs=1M count=1 oflag=direct status=none")
    run_cmd("sudo sync && sudo sysctl -w vm.drop_caches=3", check=False)
    
    # Read the data back from the virtual device
    virt_read_file = tmp_path / "virt_read.bin"
    run_cmd(f"sudo dd if={xor_dev} of={virt_read_file} bs=1M count=1 iflag=direct status=none")
    
    # Read the data from each backing device
    backing_files = []
    for i, dev in enumerate(backing_devs):
        back_file = tmp_path / f"back_{i}.bin"
        run_cmd(f"sudo dd if={dev} of={back_file} bs=1M count=1 iflag=direct status=none")
        backing_files.append(back_file)
        
    # Read all contents into memory
    with open(virt_read_file, "rb") as f:
        virt_data = f.read()
        
    backing_data_list = []
    for bf in backing_files:
        with open(bf, "rb") as f:
            backing_data_list.append(f.read())
            
    # Perform bitwise XOR across all backing data
    # int.from_bytes is extremely fast for huge byte arrays in Python
    result_int = int.from_bytes(backing_data_list[0], 'little')
    for i in range(1, len(backing_data_list)):
        current_int = int.from_bytes(backing_data_list[i], 'little')
        result_int ^= current_int
        
    result_data = result_int.to_bytes(len(virt_data), 'little')
    
    assert result_data == virt_data, "Manual XOR of backing devices does NOT match virtual device content!"
