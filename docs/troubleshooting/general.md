# MCP Memory Service Troubleshooting Guide

This guide covers common issues and their solutions when working with the MCP Memory Service.

## First-Time Setup Warnings (Normal Behavior)

### Expected Warnings on First Run

The following warnings are **completely normal** during first-time setup:

#### "No snapshots directory" Warning
```
WARNING:mcp_memory_service.storage.sqlite_vec:Failed to load from cache: No snapshots directory
```
- **Status:** ✅ Normal - Service is checking for cached models
- **Action:** None required - Model will download automatically
- **Duration:** Appears only on first run

#### "TRANSFORMERS_CACHE deprecated" Warning  
```
WARNING: Using TRANSFORMERS_CACHE is deprecated
```
- **Status:** ✅ Normal - Informational warning from Hugging Face
- **Action:** None required - Doesn't affect functionality
- **Duration:** May appear on each run (can be ignored)

#### Model Download Messages
```
Downloading model 'all-MiniLM-L6-v2'...
```
- **Status:** ✅ Normal - One-time model download (~25MB)
- **Action:** Wait 1-2 minutes for download to complete
- **Duration:** First run only

For detailed information, see the [First-Time Setup Guide](../first-time-setup.md).

## Common Installation Issues

[Content from installation.md's troubleshooting section - already well documented]

## MCP Protocol Issues

### Method Not Found Errors

If you're seeing "Method not found" errors or JSON error popups in Claude Desktop:

#### Symptoms
- "Method not found" errors in logs
- JSON error popups in Claude Desktop
- Connection issues between Claude Desktop and the memory service

#### Solution
1. Ensure you have the latest version of the MCP Memory Service
2. Verify your server implements all required MCP protocol methods:
   - resources/list
   - resources/read
   - resource_templates/list
3. Update your Claude Desktop configuration using the provided template

[Additional content from MCP_PROTOCOL_FIX.md]

## Windows-Specific Issues

[Content from WINDOWS_JSON_FIX.md and windows-specific sections]

## Performance Optimization

### Memory Issues
[Content from installation.md's performance section]

### Acceleration Issues
[Content from installation.md's acceleration section]

## Debugging Tools

[Content from installation.md's debugging section]

## Getting Help

[Content from installation.md's help section]
