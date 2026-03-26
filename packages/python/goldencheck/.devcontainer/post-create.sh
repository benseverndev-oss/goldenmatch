#!/bin/bash
pip install -e ".[dev,llm,mcp,agent]"
echo "GoldenCheck dev environment ready!"
echo "Run: goldencheck demo    # Try it out"
echo "Run: pytest --tb=short   # Run tests"
