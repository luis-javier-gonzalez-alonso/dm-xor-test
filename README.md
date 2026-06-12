# dm-xor Integration Tests

This repository contains integration tests for the `dm-xor` kernel module.

## Usage

### Quick Start (`curl | bash`)

You can easily trigger the tests on any target machine (e.g., a test VM) by running:

```bash
curl -sL https://raw.githubusercontent.com/luis-javier-gonzalez-alonso/dm-xor-test/refs/heads/main/run_tests.sh | bash
```

*Note: Replace the URL with the actual raw URL of the script once this repository is pushed to a remote.*

### Customizing the Run

The script accepts environment variables to customize its behavior. You can specify a different repository URL, branch, or target directory:

```bash
export REPO_URL="https://github.com/my-org/dm-xor-test.git"
export BRANCH="develop"
export TEST_DIR="/tmp/custom-test-dir"
curl -sL https://raw.githubusercontent.com/my-org/dm-xor-test/develop/run_tests.sh | bash
```

### Local Testing

To run the tests locally without the script:

1. Create a virtual environment: `python3 -m venv venv`
2. Activate it: `source venv/bin/activate`
3. Install requirements: `pip install -r requirements.txt`
4. Run tests: `pytest`

## Test Structure
- `test_performance.py`: Performance benchmarking and latency testing.
- `test_edge_cases.py`: Boundary conditions and error handling.
- `test_obfuscation.py`: Validation of XOR logic and data security.
- `test_io_integrity.py`: General I/O reliability tests.
- `utils/dm_helpers.py`: Helper functions for block device management.
