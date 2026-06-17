# 🧠 Code evolution analysis

Welcome to the **Code evolution analysis** tool! This project is a state-of-the-art telemetry and impact-scoring engine designed to meticulously analyze enterprise C# codebases. 

By marrying traditional compiler-level Abstract Syntax Tree (AST) analysis with modern AI neural networks, this tool measures the *true semantic impact* of every code change across your repository's history, perfectly filtering out noise such as whitespaces, reformatting, and purely cosmetic changes.

The purpose of this tool is spotting and evaluating the areas where your codebase is actually evolving more and giving you a clear overview about it; Once you will determine the "hotspots" in your codebase, you'll also be able to determine difference between versions, critical areas frequently subjected to bug fixes, or just areas where your team is putting more effort on.

---

## ✨ Key Capabilities & Sophistication

This tool does not merely count lines of code. It utilizes a **Neuro-Symbolic Engine** to evaluate code modifications across four distinct dimensions. The relative weights of these metrics are fully configurable, allowing teams to tune the pipeline for highly specific audit reports.

1. **🌳 Structural Topology (`GumTree` & `Roslyn`)**
   It analyzes the "blueprint" of the code. If a change only alters whitespace or reorders functions without changing the execution structure, the pipeline identifies it as zero-impact noise.
2. **🤖 Neural Intent Divergence (`CodeBERT` & `ONNX`)**
   Leveraging ONNX Runtime and Transformers, the pipeline asks: "Did the *meaning* of this code change?" This prevents penalizing developers for simply renaming variables or refactoring code while maintaining its original intent.
3. **🌊 Dataflow Disruption (`Tree-Sitter`)**
   It traces how data moves through the program. If a commit alters where a variable derives its value or where it sends data, the engine captures this as a significant impact.
4. **🧩 Cognitive Complexity**
   Measures whether code modifications made the logic harder or easier for a human to comprehend (e.g., heavily nested branches).

---

## 🎯 Advanced Noise Mitigation

### The Time Decay Lifespan
In a living codebase, a modification made 5 years ago is much less relevant to the current system's stability than a change made yesterday. The pipeline applies an exponential **Lifespan Time Multiplier** based on the commit's age relative to the repository's history, naturally attenuating the impact of ancient commits.

### Class-Level Aggregation & The DTO-Effect
One of the most sophisticated features of this pipeline is how it aggregates individual object scores (methods, properties, fields) up to the class level while preventing the **DTO-effect**.

The DTO-effect occurs when a brand new class is created from scratch or completely deleted, generating a massive raw score that would otherwise distort the repository's telemetry heatmap. 

To solve this, the pipeline employs a **Harmonic Rank-Based Squashing Mechanism**:
1. All individual updated object scores within a class are ranked from highest to lowest impact.
2. Before being summed, each score is **divided by its position (rank)** in the list.
3. This normalization seamlessly squashes the score of mass-changes, ensuring the overall heatmap remains impeccably balanced.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- .NET SDK (e.g., `net10.0`, `net9.0`, or `net8.0` depending on your target configuration)
- Git

### Installation

1. **Clone the repository** and navigate to the project root.
2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure your target:**
   Rename `config_example.yaml` to `config.yaml` (or edit the existing one). Update the `repo_path` to point to your target enterprise C# codebase, and adjust the `neuro_symbolic_weights` to your preference.

### Execution

Run the pipeline using the main entry point:
```bash
python main.py
```

Upon successful completion, the pipeline will generate your reports inside the `heatmap_reports/` folder, and granular JSON mappings inside the `output/` folder.

---

## 🛡️ Auditability
The pipeline supports a strict **Audit Mode** (`audit_mode: true` in config). When enabled, it writes a continuous JSONL log of its internal state, ensuring every impact score calculation—no matter how complex the neural inference—can be traced back to its raw origins.
