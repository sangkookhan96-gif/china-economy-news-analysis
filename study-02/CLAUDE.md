# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MNIST handwritten digit recognition application with dual interfaces:
- **Desktop application** using TensorFlow/Keras + Tkinter GUI
- **Web application** using PyTorch + Flask backend

## Commands

### Desktop Application
```bash
python3 digit_recognizer.py       # Run the desktop GUI app
./run_mnist.sh                    # Launch training in terminal
```

### Web Application
```bash
cd mnist_web
python3 app.py                    # Start Flask server at localhost:5000
./run_web.sh                      # Alternative launcher
```

### Model Training
```bash
python3 mnist_train.py            # Train PyTorch CNN, outputs mnist_cnn.pth
```

## Architecture

```
study-02/
├── Desktop App (TensorFlow/Keras)
│   ├── digit_recognizer.py      # Main GUI app with model inference
│   └── digit_model.h5           # Pre-trained TensorFlow model
│
├── Web App (PyTorch/Flask)
│   └── mnist_web/
│       ├── app.py               # Flask backend + CNN inference
│       ├── mnist_cnn.pth        # Pre-trained PyTorch model
│       ├── templates/index.html # Korean UI with canvas drawing
│       └── static/              # JS canvas logic + CSS
│
└── Training
    ├── mnist_train.py           # PyTorch training script
    └── data/MNIST/              # Dataset directory
```

## Key Technical Details

**CNN Architecture (both frameworks):**
- Conv32 → MaxPool → Conv64 → MaxPool → Conv64 → Dense64 → Dense10 (softmax)
- Dropout 0.5 for regularization

**Image Processing Pipeline:**
1. Canvas input (280×280) → Grayscale → Resize to 28×28
2. Normalize to [0,1] → Invert colors (white digit on black background)
3. MNIST normalization: mean=0.1307, std=0.3081

**Data Flow:**
- Desktop: Tkinter canvas → PIL Image → TensorFlow inference → Probability bars
- Web: HTML5 canvas → Base64 → Flask /predict endpoint → JSON response

## Dependencies

Core: `torch`, `tensorflow`, `torchvision`, `Pillow`, `numpy`, `Flask`, `tkinter`

## Notes

- UI text is in Korean (숫자 인식 = digit recognition)
- Models are pre-trained and included in repository
- GPU support via CUDA detection, defaults to CPU
- Flask runs in development mode (not production-ready)
