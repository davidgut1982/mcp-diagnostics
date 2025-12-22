# Contributing to diagnostic-mcp

Thank you for your interest in contributing to diagnostic-mcp! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## How to Report Bugs

Found a bug? We appreciate detailed bug reports! Here's how to report one:

1. **Check existing issues** - Search [GitHub Issues](https://github.com/latvian-lab/latvian_mcp/issues) to avoid duplicates
2. **Create a new issue** with:
   - Clear, descriptive title
   - Detailed description of the bug
   - Steps to reproduce
   - Expected vs. actual behavior
   - Your environment (Python version, OS, etc.)
   - Relevant logs or error messages
   - Code examples if applicable

## How to Suggest Features

Have an idea for an improvement? We'd love to hear it!

1. **Check existing issues/discussions** - Someone may have already suggested it
2. **Create a GitHub issue** with the `enhancement` label including:
   - Clear problem statement (what's missing/what's wrong?)
   - Proposed solution
   - Alternative approaches you've considered
   - Use cases and examples
   - Why this feature would be valuable

## Development Setup

### Prerequisites
- Python 3.8+
- pip and virtualenv
- Git

### Local Setup

```bash
# Clone the repository
git clone https://github.com/latvian-lab/latvian_mcp.git
cd latvian_mcp/servers/diagnostic-mcp

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

### Installation with Dev Dependencies

The `.[dev]` installation includes:
- Testing framework (pytest)
- Code formatter (black)
- Linting tools
- Other development utilities

If `setup.py` doesn't yet include dev extras, install manually:

```bash
pip install -r requirements.txt
pip install pytest black pytest-cov
```

## Running Tests

We use `pytest` for testing. Run tests before submitting any changes:

```bash
# Run all tests
pytest tests/

# Run with coverage report
pytest --cov=src tests/

# Run specific test file
pytest tests/test_health_check.py

# Run with verbose output
pytest -v tests/
```

All tests must pass before submitting a pull request.

## Code Style

We use **Black** for consistent code formatting. All submissions must be formatted with Black.

```bash
# Format all files in src/
black src/

# Format specific file
black src/diagnostic_mcp/server.py

# Check formatting without changes
black --check src/
```

### Code Style Guidelines

- **Line length**: Black's default (88 characters)
- **Imports**: Organized as: stdlib → third-party → local
- **Docstrings**: Use triple-quoted strings for functions and classes
- **Type hints**: Encouraged where applicable
- **Comments**: Explain *why*, not *what* the code does

Example:

```python
def check_port_consistency() -> Dict[str, Any]:
    """
    Validate port assignments across all MCP servers.
    
    Returns:
        Dict with port mapping, conflicts, gaps, and summary.
        
    Raises:
        ValueError: If settings file is malformed.
    """
    # Implementation here
    pass
```

## Pull Request Process

### Before You Start

1. Fork the repository
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. Make your changes in small, logical commits
4. Keep your branch updated with upstream `main`

### Submitting a Pull Request

1. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open a Pull Request** on GitHub with:
   - Clear title describing the change
   - Reference any related issues (e.g., "Fixes #42")
   - Detailed description of what changed and why
   - List of testing performed

3. **PR Description Template**:
   ```
   ## Description
   Brief description of changes
   
   ## Related Issues
   Fixes #42
   
   ## Changes Made
   - Change 1
   - Change 2
   - Change 3
   
   ## Testing Performed
   - [ ] Unit tests pass
   - [ ] All tests pass
   - [ ] Manual testing done
   - [ ] Tested on [environment]
   
   ## Type of Change
   - [ ] Bug fix (non-breaking)
   - [ ] New feature (non-breaking)
   - [ ] Breaking change
   - [ ] Documentation update
   ```

4. **Respond to feedback** - Maintainers may request changes or clarifications

### PR Review Checklist

Your PR will be reviewed for:

- [ ] **Tests** - All new code has tests, all tests pass
- [ ] **Code style** - Formatted with Black, follows conventions
- [ ] **Documentation** - Docstrings, comments, README updates if needed
- [ ] **No breaking changes** - Backward compatibility maintained (unless explicitly noted)
- [ ] **Commits are clean** - Logical, descriptive commit messages

## Testing Requirements Before PR

**All pull requests must include:**

1. **Unit tests** for new functionality
   - Test success cases
   - Test error cases
   - Test edge cases

2. **Pass existing tests**
   ```bash
   pytest tests/
   ```

3. **Code coverage** - Maintain or improve coverage
   ```bash
   pytest --cov=src tests/
   ```

4. **Manual testing** - Test the feature manually if applicable

5. **Documentation** - Update docstrings, README, or docs as needed

## Example Contribution Workflow

```bash
# 1. Create and checkout feature branch
git checkout -b feature/add-timeout-validation

# 2. Make changes
vim src/diagnostic_mcp/health.py

# 3. Add tests
vim tests/test_health.py

# 4. Run tests
pytest tests/test_health.py -v

# 5. Format code
black src/diagnostic_mcp/health.py

# 6. Verify all tests still pass
pytest tests/

# 7. Commit
git add src/ tests/
git commit -m "feat: add timeout validation for health checks"

# 8. Push and create PR
git push origin feature/add-timeout-validation
```

## Questions?

- Check existing [GitHub Issues](https://github.com/latvian-lab/latvian_mcp/issues) and discussions
- Review the [README](README.md) and documentation
- Open a GitHub discussion for questions

## License

By contributing to diagnostic-mcp, you agree that your contributions will be licensed under the same license as the project.

---

Thank you for contributing to diagnostic-mcp! Your efforts help make the MCP infrastructure more robust and reliable.
