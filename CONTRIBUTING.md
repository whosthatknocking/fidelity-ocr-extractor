# Contributing

Thank you for your interest in contributing to Fidelity OCR Extractor! This document provides guidelines and information for contributors.

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/whosthatknocking/fidelity-ocr-extractor.git
   cd fidelity-ocr-extractor
   ```

2. **Set up development environment**
   ```bash
   pip install -e .[dev]
   ```

3. **Run tests**
   ```bash
   pytest
   ```

4. **Check code quality**
   ```bash
   flake8
   ```

## Development Workflow

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Follow the existing code style
   - Add tests for new functionality
   - Update documentation as needed

3. **Run quality checks**
   ```bash
   pytest  # Run tests
   flake8  # Check code style
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "Add: brief description of changes"
   ```

5. **Push and create pull request**
   ```bash
   git push origin feature/your-feature-name
   ```

## Code Style

This project follows PEP 8 with some modifications:
- Maximum line length: 127 characters
- Maximum complexity: 10

Use `flake8` to check compliance:
```bash
flake8
```

## Testing

- Add tests for new features in the `tests/` directory
- Ensure all tests pass before submitting a pull request
- Aim for good test coverage

## Documentation

- Update README.md for user-facing changes
- Update docstrings for code changes
- Add documentation in `docs/` for complex features

## Commit Messages

Use conventional commit format:
- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `style:` - Code style changes
- `refactor:` - Code refactoring
- `test:` - Test additions/changes

## Pull Request Process

1. Ensure your branch is up to date with main
2. Run all tests and linting
3. Create a clear PR description
4. Request review from maintainers

## Questions?

Feel free to open an issue for questions or discussions about the project.