#!/usr/bin/env python3
"""
MCP Memory Service - Admin UI Installer
Sets up Streamlit-based admin interface with HTTP client dependencies
"""

import sys
import subprocess
import platform
from pathlib import Path


def check_python_version():
    """Ensure Python 3.9+"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print("❌ Python 3.9 or higher is required")
        print(f"   Current version: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")


def create_venv():
    """Create virtual environment for admin UI"""
    venv_path = Path("venv-admin")

    if venv_path.exists():
        print("⚠️  venv-admin already exists, skipping creation")
        return venv_path

    print("📦 Creating venv-admin virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", "venv-admin"], check=True)
    print("✅ Virtual environment created")
    return venv_path


def get_pip_path(venv_path: Path) -> Path:
    """Get pip executable path for the platform"""
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    else:
        return venv_path / "bin" / "pip"


def install_dependencies(venv_path: Path):
    """Install admin UI dependencies"""
    pip_path = get_pip_path(venv_path)

    print("\n📥 Installing admin UI dependencies...")
    subprocess.run([
        str(pip_path), "install", "-r", "requirements-admin.txt"
    ], check=True)
    print("✅ Dependencies installed")


def validate_installation(venv_path: Path):
    """Verify streamlit is installed"""
    python_path = venv_path / "bin" / "python" if platform.system() != "Windows" else venv_path / "Scripts" / "python.exe"

    print("\n🔍 Validating installation...")
    result = subprocess.run([
        str(python_path), "-c", "import streamlit; print(streamlit.__version__)"
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ Streamlit {result.stdout.strip()} installed successfully")
    else:
        print("❌ Streamlit installation validation failed")
        sys.exit(1)


def print_usage():
    """Print usage instructions"""
    print("\n" + "="*60)
    print("🎉 Admin UI installation complete!")
    print("="*60)
    print("\nUsage:")
    print("  ./run_admin.sh                          # Connect to localhost:8030")
    print("  ./run_admin.sh http://mevault:8030/mcp  # Connect to remote server")
    print("\nEnvironment variable:")
    print("  export MCP_SERVER_URL=http://localhost:8030/mcp")
    print("  ./run_admin.sh")
    print("\n" + "="*60)


def main():
    print("🧠 MCP Memory Service - Admin UI Installer")
    print("="*60)

    check_python_version()
    venv_path = create_venv()
    install_dependencies(venv_path)
    validate_installation(venv_path)
    print_usage()


if __name__ == "__main__":
    main()
