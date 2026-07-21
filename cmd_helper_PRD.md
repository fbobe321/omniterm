Here is a draft for the Product Requirements Document (PRD) to structure the development and deployment of the shadow command predictor for OmniTerm.

## Product Requirements Document: OmniTerm Shadow Command Predictor

### 1. Overview & Objectives

* **Product:** OmniTerm
* **Feature:** Local Shadow Command Predictor
* **Concept:** An ultra-lightweight, on-device language model integrated directly into OmniTerm's input loop. It analyzes recent command history and the current input buffer to predict the user's next command, displaying it as inline shadow text.
* **Key Objective:** Deliver an AI-assisted terminal experience that accelerates workflows while maintaining complete data privacy and a minimal local footprint.

### 2. User Experience (UX) & Interaction

* **Visual Feedback:** Predicted text appears immediately ahead of the cursor in a low-contrast, dim ANSI color (e.g., dark gray or faded blue).
* **Acceptance Mechanics:**
* Pressing `Tab` populates the command prompt with the full shadow text.
* Once populated, the command acts as standard input. The user can edit the command using standard keybindings before pressing `Enter` for execution.


* **Latency:** Inference and rendering must complete in `< 50ms` to prevent UI blocking or typing stutter.
* **Configuration:** The OmniTerm settings menu (and configuration file) must include:
* `shadow_predictor.enabled`: Boolean toggle (default `True`).
* `shadow_predictor.aggressiveness`: Threshold for prediction confidence before rendering shadow text.
* `shadow_predictor.history_depth`: How many previous commands to include in the context window.



### 3. Model Architecture & Constraints

To keep the footprint under 50M parameters and ensure fast CPU inference, the model requires a hyper-optimized, custom architecture.

* **Architecture:** A minimal decoder-only Transformer (e.g., 4-6 layers, hidden size of 256-512, 8 attention heads).
* **Vocabulary:** A heavily pruned, domain-specific tokenizer (BPE or Unigram) optimized specifically for CLI syntax (paths, common flags, bash/python commands) rather than natural language. Target vocabulary size: `~8,192` tokens. Shrinking the embedding matrix is critical, as it dominates the parameter count in very small models.
* **Context Window:** Restricted to `256` or `512` tokens, capturing only the immediate session history and the current working directory state.

### 4. Deployment & Packaging Strategy

The feature must distribute seamlessly via GitHub and PyPI without requiring users to compile custom C++ runtimes or install massive ML frameworks.

* **Inference Engine:** Pure PyTorch is too heavy for a terminal emulator dependency. The trained model should be exported to **ONNX** and executed using `onnxruntime` (CPU execution provider). It is pip-installable, highly portable across OS platforms, and highly optimized for small CPU workloads.
* **Weight Distribution:**
* At ~30M-40M parameters, applying `int8` quantization will reduce the model size to roughly **30MB to 40MB**.
* **Lazy Loading:** Rather than bloating the PyPI wheel, OmniTerm should fetch the quantized `.onnx` model file from a GitHub Release asset on the first run (or when the user enables the feature) and cache it in `~/.local/share/omniterm/models/`.


* **Dependency Isolation:** Make the predictor an optional extra to keep the core terminal lightweight (e.g., users install via `pip install omniterm[ai]`).

### 5. Security & Edge Cases

* **Sensitive Data:** The input interceptor must pause prediction and history-tracking when the terminal is in a masked input state (e.g., `sudo` password prompts) to prevent memorizing secrets.
* **Execution Safety:** The model strictly provides text rendering. It is physically isolated from the execution thread; the user must explicitly accept and manually execute every command.