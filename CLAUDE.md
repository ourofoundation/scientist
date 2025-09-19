# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI-powered materials discovery system called "scientist" that uses DSPy to discover rare-earth-free permanent magnets. It combines large language models with computational materials science tools through the Ouro platform and GGen library to systematically generate, evaluate, mutate, and refine material candidates with full mutation history tracking.

## Key Architecture

The system follows a DSPy-based iterative discovery loop:

1. **Hypothesis Generation/Refinement** (`GenerateMagnetHypothesis`, `RefineHypothesis`): Uses LLMs to generate scientific hypotheses about potential magnetic materials, incorporating mutation history feedback
2. **Material Design** (`DesignMaterialCandidate`): Converts hypotheses into specific chemical compositions and crystal structures, or selects parent materials for mutation
3. **Mutation Strategy Selection** (`SelectMutationStrategy`): AI-driven selection of mutation operations (scale lattice, substitute elements, jitter sites, etc.) based on performance analysis
4. **Structure Generation/Mutation** (`ComputationalTools` + `GGen`): Generates new crystal structures from scratch or applies mutations to existing materials using GGen library
5. **Computational Evaluation** (`ComputationalTools`): Evaluates material properties using Ouro routes with mutation effect tracking
6. **Results Interpretation** (`InterpretSimulationResults`): Analyzes computational results and extracts insights, including mutation effectiveness analysis

### Core Components

- **`app.py`**: Main application with `MaterialDiscoveryScientist` DSPy module and mutation-aware discovery loop
- **`tools.py`**: `ComputationalTools` class that interfaces with Ouro platform and GGen library for structure generation, mutation, and property evaluation
- **`models.py`**: Data structures (`Material`, `MaterialProperties`, `MutationRecord`) for representing materials, properties, and complete mutation lineage
- **`publisher.py`**: `Publisher` class for creating Ouro posts with discovery results, mutation analysis, and visualizations

### Mutation System

The system supports intelligent material mutation through GGen:
- **Mutation Types**: Scale lattice, substitute elements, jitter atomic sites, shear lattice, break symmetry, change space groups
- **History Tracking**: Complete lineage of mutations with success/failure rates and property changes
- **Strategy Selection**: AI selects optimal mutation strategies based on performance analysis and target properties
- **Fallback Generation**: Falls back to Ouro crystal generation if GGen mutations fail

### DSPy Integration

The system uses DSPy signatures for structured LLM reasoning:
- Input/output fields are clearly defined for each reasoning step
- ChainOfThought modules provide detailed reasoning traces
- **Mutation-aware signatures**: `SelectMutationStrategy` for intelligent mutation selection, updated `RefineHypothesis` and `InterpretSimulationResults` with mutation history context
- MLflow integration tracks optimization and evaluation metrics including mutation success rates

## Essential Commands

### Running the Application
```bash
# Run the main discovery loop
python app.py
# or
scientist  # via setuptools entry point
```

### MLflow Server (Required)
```bash
# Start MLflow tracking server (must be running before app execution)
mlflow server --backend-store-uri sqlite:///scientist.sqlite
```

### Package Management
```bash
# Install in development mode
pip install -e .
```

## Environment Setup

Required environment variables (see `.env.example`):
- `OPENAI_API_KEY`: OpenAI API key for DSPy LLM calls
- `OURO_API_KEY`: Ouro platform API key for computational tools
- `OURO_TEAM_ID`: Ouro team ID for file/asset management

## Development Workflow

1. Ensure MLflow server is running locally on port 5000
2. Configure environment variables in `.env` file
3. Run the discovery loop with `python app.py`
4. The system will:
   - Start with random generation for first few iterations
   - Begin applying intelligent mutations based on best candidates
   - Track mutation success rates and property changes
   - Adapt strategy selection based on performance analysis
5. Results are automatically logged to MLflow and published to Ouro
6. Generated structures, mutations, and property evaluations are stored as artifacts
7. Mutation history and lineage are preserved for analysis

## Key Dependencies

- **DSPy**: Framework for optimizing LLM-based reasoning
- **GGen**: Crystal generation and mutation library (local dependency at `/Users/mmoderwell/ouro/ggen`)
- **PyMatGen**: Materials science library for crystal structures
- **MLflow**: Experiment tracking and model management
- **Ouro-py**: Interface to Ouro computational platform
- **OpenAI**: LLM provider for hypothesis generation

## File Structure Notes

- `generation/`: Contains additional modules (ignore per user request)
- `mlartifacts/`: MLflow artifact storage directory
- `scientist.sqlite`: MLflow backend database
- Material registry stores mutation lineage in memory during execution
- GGen integration allows fallback between GGen and Ouro generation methods
- Space group compatibility checking and resolution is handled automatically by the Ouro platform
- GGen library provides trajectory tracking for mutation sequences
- Material registry maintains relationships between parent and child materials for mutation lineage analysis
- Mutation success rates inform future strategy selection for continuous improvement