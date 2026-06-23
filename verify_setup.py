"""
Verify project setup and dependencies.
"""

import sys
from pathlib import Path

def check_python_version():
    """Check Python version."""
    version = sys.version_info
    print(f"✓ Python version: {version.major}.{version.minor}.{version.micro}")
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print("  ⚠ Warning: Python 3.10+ recommended")
        return False
    return True

def check_imports():
    """Check if required packages can be imported."""
    packages = [
        ("neo4j", "Neo4j driver"),
        ("anthropic", "Anthropic API"),
        ("langchain", "LangChain"),
        ("langchain_anthropic", "LangChain Anthropic"),
        ("fastapi", "FastAPI"),
        ("pydantic", "Pydantic"),
        ("loguru", "Loguru"),
    ]
    
    all_ok = True
    for package, name in packages:
        try:
            __import__(package)
            print(f"✓ {name}: installed")
        except ImportError:
            print(f"✗ {name}: NOT installed")
            all_ok = False
    
    return all_ok

def get_project_root():
    """Find project root by looking for key files."""
    current = Path(__file__).resolve()
    
    # If script is in scripts/ directory, parent is project root
    if current.parent.name == "scripts":
        return current.parent.parent
    
    # If script is in project root, use current directory
    if (current.parent / "src").exists() and (current.parent / "requirements.txt").exists():
        return current.parent
    
    # Search upwards for project root
    for parent in current.parents:
        if (parent / "src").exists() and (parent / "requirements.txt").exists():
            return parent
    
    # Default to current directory's parent
    return current.parent

def check_project_structure():
    """Check if project structure is correct."""
    root = get_project_root()
    print(f"Project root detected: {root}")
    
    required_dirs = [
        "src",
        "src/graph",
        "src/ingestion",
        "src/reasoning",
        "src/api",
        "scripts",
        "data",
        "docs",
    ]
    
    required_files = [
        "src/__init__.py",
        "src/config.py",
        "src/graph/__init__.py",
        "src/graph/neo4j_client.py",
        "src/ingestion/__init__.py",
        "src/ingestion/entity_extractor.py",
        "requirements.txt",
        "docker-compose.yml",
        ".env.example",
    ]
    
    all_ok = True
    
    print("\nChecking directories:")
    for dir_path in required_dirs:
        full_path = root / dir_path
        if full_path.exists():
            print(f"✓ {dir_path}/")
        else:
            print(f"✗ {dir_path}/ NOT FOUND")
            all_ok = False
    
    print("\nChecking files:")
    for file_path in required_files:
        full_path = root / file_path
        if full_path.exists():
            print(f"✓ {file_path}")
        else:
            print(f"✗ {file_path} NOT FOUND")
            all_ok = False
    
    return all_ok

def check_env_file():
    """Check if .env file exists and has required variables."""
    root = get_project_root()
    env_file = root / ".env"
    
    if not env_file.exists():
        print("\n⚠ .env file not found")
        print("  Copy .env.example to .env and add your API keys:")
        print("  cp .env.example .env")
        return False
    
    print("\n✓ .env file exists")
    
    # Check for required keys (without reading actual values)
    required_keys = [
        "ANTHROPIC_API_KEY",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
    ]
    
    with open(env_file) as f:
        content = f.read()
    
    missing = []
    for key in required_keys:
        if key not in content:
            missing.append(key)
    
    if missing:
        print(f"  ⚠ Missing keys: {', '.join(missing)}")
        return False
    
    print("  ✓ All required environment variables present")
    return True

def check_neo4j_connection():
    """Check if Neo4j is running and accessible."""
    try:
        # Add project root to path
        root = get_project_root()
        sys.path.insert(0, str(root))
        from src.graph.neo4j_client import Neo4jClient
        
        print("\nChecking Neo4j connection...")
        client = Neo4jClient()
        client.connect()
        
        # Try a simple query
        result = client.execute_query("RETURN 1 as test")
        if result and result[0].get('test') == 1:
            print("✓ Neo4j is running and accessible")
            client.close()
            return True
        else:
            print("✗ Neo4j query failed")
            client.close()
            return False
            
    except Exception as e:
        print(f"✗ Neo4j connection failed: {e}")
        print("\n  Make sure Neo4j is running:")
        print("  docker-compose up -d neo4j")
        return False

def check_data_directory():
    """Check if sample data exists."""
    root = get_project_root()
    data_dir = root / "data" / "sample"
    
    print("\nChecking sample data:")
    
    if not data_dir.exists():
        print("✗ data/sample/ directory not found")
        print("\n  Generate sample data:")
        print("  python scripts/generate_sample_data.py")
        return False
    
    required_files = [
        "parts.json",
        "suppliers.json",
        "supply_relationships.json",
        "compatibility.json",
    ]
    
    all_ok = True
    for filename in required_files:
        filepath = data_dir / filename
        if filepath.exists():
            print(f"✓ {filename}")
        else:
            print(f"✗ {filename} NOT FOUND")
            all_ok = False
    
    if not all_ok:
        print("\n  Generate sample data:")
        print("  python scripts/generate_sample_data.py")
    
    return all_ok

def main():
    """Run all checks."""
    print("=" * 70)
    print("SUPPLY CHAIN KNOWLEDGE GRAPH - SETUP VERIFICATION")
    print("=" * 70)
    
    checks = [
        ("Python Version", check_python_version),
        ("Python Packages", check_imports),
        ("Project Structure", check_project_structure),
        ("Environment File", check_env_file),
        ("Sample Data", check_data_directory),
        ("Neo4j Connection", check_neo4j_connection),
    ]
    
    results = {}
    for name, check_func in checks:
        print(f"\n{'='*70}")
        print(f"Checking: {name}")
        print("=" * 70)
        try:
            results[name] = check_func()
        except Exception as e:
            print(f"✗ Check failed with error: {e}")
            results[name] = False
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n" + "=" * 70)
        print("✓ ALL CHECKS PASSED!")
        print("=" * 70)
        print("\nYou're ready to go! Next steps:")
        print("1. Generate sample data: python scripts/generate_sample_data.py")
        print("2. Load into Neo4j: python scripts/load_sample_data.py")
        print("3. Run Claude demo: python examples/claude_extraction_demo.py")
    else:
        print("\n" + "=" * 70)
        print("⚠ SOME CHECKS FAILED")
        print("=" * 70)
        print("\nFix the issues above and run this script again.")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)