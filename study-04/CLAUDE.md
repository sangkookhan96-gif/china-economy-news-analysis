# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based project that uses the OpenRouter API for accessing LLM models. The project is in early development stage.

## Environment Setup

- Uses `.env` file for `OPENROUTER_API_KEY`
- Python virtual environment recommended (`venv/`)

## Available Models

Configured models (in `models.txt`):
- `google/gemma-3-27b-it:free`
- `deepseek/deepseek-chat-v3.1`

## Setup

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

## Usage

```python
from openrouter_client import chat

response = chat("Hello!")
```

## Project Structure

- `config.py` - Loads API key from .env
- `openrouter_client.py` - OpenRouter API client
