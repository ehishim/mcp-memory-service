# First-Time Setup Guide

This guide explains what to expect when running MCP Memory Service for the first time.

## 🎯 What to Expect on First Run

When you start the MCP Memory Service for the first time, you'll see several warnings and messages. **This is completely normal behavior** as the service initializes and downloads necessary components.

## 📋 Normal First-Time Warnings

### 1. Snapshots Directory Warning
```
WARNING:mcp_memory_service.storage.sqlite_vec:Failed to load from cache: No snapshots directory
```

**What it means:** 
- The service is checking for previously downloaded embedding models
- On first run, no cache exists yet, so this warning appears
- The service will automatically download the model

**This is normal:** ✅ Expected on first run

### 2. TRANSFORMERS_CACHE Warning
```
WARNING: Using TRANSFORMERS_CACHE is deprecated
```

**What it means:**
- This is an informational warning from the Hugging Face library
- It doesn't affect the service functionality
- The service handles caching internally

**This is normal:** ✅ Can be safely ignored

### 3. Model Download Progress
```
Downloading model 'all-MiniLM-L6-v2'...
```

**What it means:**
- The service is downloading the embedding model (approximately 25MB)
- This happens only once on first setup
- Download time: 1-2 minutes on average internet connection

**This is normal:** ✅ One-time download

## 🚦 Success Indicators

After successful first-time setup, you should see:

```
INFO: SQLite-vec storage initialized successfully with embedding dimension: 384
INFO: Memory service started on port 8443
INFO: Ready to accept connections
```

## 📊 First-Time Setup Timeline

| Step | Duration | What's Happening |
|------|----------|-----------------|
| 1. Service Start | Instant | Loading configuration |
| 2. Cache Check | 1-2 seconds | Checking for existing models |
| 3. Model Download | 1-2 minutes | Downloading embedding model (~25MB) |
| 4. Model Loading | 5-10 seconds | Loading model into memory |
| 5. Database Init | 2-3 seconds | Creating database structure |
| 6. Ready | - | Service is ready to use |

**Total first-time setup:** 2-3 minutes

## 🔄 Subsequent Runs

After the first successful run:
- No download warnings will appear
- Model loads from cache (5-10 seconds)
- Service starts much faster (10-15 seconds total)

## 🐧 Ubuntu/Linux Specific Notes

For Ubuntu 24 and other Linux distributions:

### Prerequisites
```bash
# System dependencies
sudo apt update
sudo apt install python3.10 python3.10-venv python3.10-dev python3-pip
sudo apt install build-essential libblas3 liblapack3 liblapack-dev libblas-dev gfortran
```

### Recommended Setup
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the service
python install.py

# Start the service
uv run memory server
```

## 🔧 Troubleshooting First-Time Issues

### Issue: Download Fails
**Solution:**
- Check internet connection
- Verify firewall/proxy settings
- Clear cache and retry: `rm -rf ~/.cache/huggingface`

### Issue: "No module named 'sentence_transformers'"
**Solution:**
```bash
pip install sentence-transformers torch
```

### Issue: Permission Denied
**Solution:**
```bash
# Fix permissions
chmod +x scripts/*.sh
sudo chown -R $USER:$USER ~/.mcp_memory_service/
```

### Issue: Service Doesn't Start After Download
**Solution:**
1. Check logs: `uv run memory server --debug`
2. Verify installation: `python scripts/verify_environment.py`
3. Restart with clean state: 
   ```bash
   rm -rf ~/.mcp_memory_service
   uv run memory server
   ```

## ✅ Verification

To verify successful setup:

```bash
# Check service health
curl -k https://localhost:8443/api/health

# Or using the CLI
uv run memory health
```

Expected response:
```json
{
  "status": "healthy",
  "storage_backend": "sqlite_vec",
  "model_loaded": true
}
```

## 🎉 Setup Complete!

Once you see the success indicators and the warnings have disappeared on subsequent runs, your MCP Memory Service is fully operational and ready to use!

### Next Steps:
- [Configure Claude Desktop](../README.md#claude-desktop-integration)
- [Store your first memory](../README.md#basic-usage)
- [Explore the API](https://github.com/doobidoo/mcp-memory-service/wiki)

## 📝 Notes

- The model download is a one-time operation
- Downloaded models are cached in `~/.cache/huggingface/`
- The service creates a database in `~/.mcp_memory_service/`
- All warnings shown during first-time setup are expected behavior
- If you see different errors (not warnings), check the [Troubleshooting Guide](troubleshooting/general.md)

---

Remember: **First-time warnings are normal!** The service is working correctly and setting itself up for optimal performance.