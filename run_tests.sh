#!/bin/bash
set -e

# Configuration (can be overridden by environment variables)
REPO_URL=${REPO_URL:-"https://github.com/luis-javier-gonzalez-alonso/dm-xor-test.git"}
BRANCH=${BRANCH:-"main"}
TEST_DIR=${TEST_DIR:-"/tmp/dm-xor-tests-env"}

echo "========================================"
echo " Preparing dm-xor tests environment"
echo "========================================"

# Ensure required commands are installed
for cmd in git python3; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is required but not installed."
        exit 1
    fi
done

NEEDS_UPDATE=0

if [ -d "$TEST_DIR/.git" ]; then
    echo "Found existing test directory at $TEST_DIR"
    cd "$TEST_DIR"
    
    echo "Checking for updates on branch '$BRANCH'..."
    git fetch origin "$BRANCH"
    
    LOCAL_HASH=$(git rev-parse HEAD)
    REMOTE_HASH=$(git rev-parse origin/"$BRANCH")
    
    if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
        echo "Tests have changed. Updating to latest version ($REMOTE_HASH)..."
        git reset --hard "$REMOTE_HASH"
        NEEDS_UPDATE=1
    else
        echo "Tests have not changed since last run."
    fi
else
    echo "Cloning test repository from $REPO_URL..."
    # Remove directory if it exists but is not a valid git repository
    rm -rf "$TEST_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$TEST_DIR"
    cd "$TEST_DIR"
    NEEDS_UPDATE=1
fi

# Setup Python Virtual Environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment in $TEST_DIR/venv..."
    python3 -m venv venv
    NEEDS_UPDATE=1
fi

# Activate virtual environment
source venv/bin/activate

# Install/Update requirements if needed
if [ "$NEEDS_UPDATE" -eq 1 ]; then
    echo "Installing Python requirements..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    echo "Skipping requirements installation (no changes detected)."
fi

echo "========================================"
echo " Triggering Tests"
echo "========================================"
pytest
