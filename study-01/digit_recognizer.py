#!/usr/bin/env python3
"""
Handwritten Digit Recognition Application
Uses a CNN model trained on MNIST dataset with a tkinter GUI for drawing digits.
"""

import os
import numpy as np
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk

# Suppress TensorFlow warnings
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers


class DigitRecognitionModel:
    """CNN model for handwritten digit recognition."""

    def __init__(self, model_path='digit_model.h5'):
        self.model_path = model_path
        self.model = None
        self.load_or_train_model()

    def create_model(self):
        """Create a CNN model architecture for digit recognition."""
        model = keras.Sequential([
            layers.Input(shape=(28, 28, 1)),
            layers.Conv2D(32, (3, 3), activation='relu'),
            layers.MaxPooling2D((2, 2)),
            layers.Conv2D(64, (3, 3), activation='relu'),
            layers.MaxPooling2D((2, 2)),
            layers.Conv2D(64, (3, 3), activation='relu'),
            layers.Flatten(),
            layers.Dense(64, activation='relu'),
            layers.Dropout(0.5),
            layers.Dense(10, activation='softmax')
        ])

        model.compile(
            optimizer='adam',
            loss='sparse_categorical_crossentropy',
            metrics=['accuracy']
        )
        return model

    def load_or_train_model(self):
        """Load existing model or train a new one."""
        if os.path.exists(self.model_path):
            print("Loading existing model...")
            self.model = keras.models.load_model(self.model_path)
            print("Model loaded successfully!")
        else:
            print("Training new model on MNIST dataset...")
            self.train_model()

    def train_model(self):
        """Train the model on MNIST dataset."""
        # Load MNIST dataset
        (x_train, y_train), (x_test, y_test) = keras.datasets.mnist.load_data()

        # Normalize pixel values to [0, 1]
        x_train = x_train.astype('float32') / 255.0
        x_test = x_test.astype('float32') / 255.0

        # Add channel dimension
        x_train = x_train.reshape(-1, 28, 28, 1)
        x_test = x_test.reshape(-1, 28, 28, 1)

        # Create and train model
        self.model = self.create_model()

        print("Training model (this may take a few minutes)...")
        self.model.fit(
            x_train, y_train,
            epochs=5,
            batch_size=64,
            validation_split=0.1,
            verbose=1
        )

        # Evaluate on test set
        test_loss, test_acc = self.model.evaluate(x_test, y_test, verbose=0)
        print(f"Test accuracy: {test_acc:.4f}")

        # Save model
        self.model.save(self.model_path)
        print(f"Model saved to {self.model_path}")

    def predict(self, image):
        """
        Predict the digit in the given image.

        Args:
            image: PIL Image object (grayscale, any size)

        Returns:
            tuple: (predicted_digit, confidence, all_probabilities)
        """
        # Convert to grayscale if needed
        if image.mode != 'L':
            image = image.convert('L')

        # Resize to 28x28
        image = image.resize((28, 28), Image.LANCZOS)

        # Convert to numpy array and normalize
        img_array = np.array(image).astype('float32') / 255.0

        # Invert colors (MNIST has white digits on black background)
        img_array = 1.0 - img_array

        # Add batch and channel dimensions
        img_array = img_array.reshape(1, 28, 28, 1)

        # Get predictions
        predictions = self.model.predict(img_array, verbose=0)[0]
        predicted_digit = np.argmax(predictions)
        confidence = predictions[predicted_digit]

        return predicted_digit, confidence, predictions


class DrawingCanvas:
    """Canvas widget for drawing digits."""

    def __init__(self, parent, size=280):
        self.size = size
        self.brush_size = 15

        # Create canvas
        self.canvas = tk.Canvas(
            parent,
            width=size,
            height=size,
            bg='white',
            cursor='cross'
        )
        self.canvas.pack(pady=10)

        # Create PIL image for drawing
        self.image = Image.new('L', (size, size), 'white')
        self.draw = ImageDraw.Draw(self.image)

        # Bind mouse events
        self.canvas.bind('<B1-Motion>', self.paint)
        self.canvas.bind('<Button-1>', self.paint)

        self.last_x = None
        self.last_y = None
        self.canvas.bind('<ButtonRelease-1>', self.reset_position)

    def paint(self, event):
        """Draw on the canvas."""
        x, y = event.x, event.y
        r = self.brush_size

        # Draw on canvas
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r,
            fill='black', outline='black'
        )

        # Draw on PIL image
        self.draw.ellipse([x - r, y - r, x + r, y + r], fill='black')

        # Draw line to connect points for smooth strokes
        if self.last_x is not None and self.last_y is not None:
            self.canvas.create_line(
                self.last_x, self.last_y, x, y,
                width=r * 2, fill='black', capstyle=tk.ROUND
            )
            self.draw.line(
                [self.last_x, self.last_y, x, y],
                fill='black', width=r * 2
            )

        self.last_x = x
        self.last_y = y

    def reset_position(self, event):
        """Reset last position when mouse button is released."""
        self.last_x = None
        self.last_y = None

    def clear(self):
        """Clear the canvas."""
        self.canvas.delete('all')
        self.image = Image.new('L', (self.size, self.size), 'white')
        self.draw = ImageDraw.Draw(self.image)

    def get_image(self):
        """Get the current drawing as a PIL Image."""
        return self.image.copy()


class DigitRecognizerApp:
    """Main application window."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Handwritten Digit Recognizer")
        self.root.resizable(False, False)

        # Load model
        self.model = DigitRecognitionModel()

        self.setup_ui()

    def setup_ui(self):
        """Set up the user interface."""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack()

        # Title
        title_label = ttk.Label(
            main_frame,
            text="Draw a digit (0-9)",
            font=('Arial', 16, 'bold')
        )
        title_label.pack(pady=5)

        # Drawing canvas
        self.canvas = DrawingCanvas(main_frame)

        # Buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)

        # Recognize button
        recognize_btn = ttk.Button(
            button_frame,
            text="Recognize",
            command=self.recognize_digit
        )
        recognize_btn.pack(side=tk.LEFT, padx=5)

        # Clear button
        clear_btn = ttk.Button(
            button_frame,
            text="Clear",
            command=self.clear_canvas
        )
        clear_btn.pack(side=tk.LEFT, padx=5)

        # Result display
        self.result_var = tk.StringVar(value="Draw a digit and click 'Recognize'")
        result_label = ttk.Label(
            main_frame,
            textvariable=self.result_var,
            font=('Arial', 14)
        )
        result_label.pack(pady=10)

        # Prediction display (large digit)
        self.prediction_var = tk.StringVar(value="?")
        prediction_label = ttk.Label(
            main_frame,
            textvariable=self.prediction_var,
            font=('Arial', 72, 'bold'),
            foreground='blue'
        )
        prediction_label.pack(pady=5)

        # Probability bars frame
        prob_frame = ttk.LabelFrame(main_frame, text="Probabilities", padding="5")
        prob_frame.pack(pady=10, fill=tk.X)

        self.prob_bars = []
        self.prob_labels = []

        for i in range(10):
            row_frame = ttk.Frame(prob_frame)
            row_frame.pack(fill=tk.X, pady=1)

            # Digit label
            digit_label = ttk.Label(row_frame, text=str(i), width=2)
            digit_label.pack(side=tk.LEFT)

            # Progress bar
            prob_bar = ttk.Progressbar(
                row_frame,
                length=200,
                mode='determinate'
            )
            prob_bar.pack(side=tk.LEFT, padx=5)
            self.prob_bars.append(prob_bar)

            # Percentage label
            pct_label = ttk.Label(row_frame, text="0%", width=6)
            pct_label.pack(side=tk.LEFT)
            self.prob_labels.append(pct_label)

    def recognize_digit(self):
        """Recognize the drawn digit."""
        image = self.canvas.get_image()
        predicted_digit, confidence, probabilities = self.model.predict(image)

        # Update result display
        self.result_var.set(f"Prediction: {predicted_digit} (Confidence: {confidence:.1%})")
        self.prediction_var.set(str(predicted_digit))

        # Update probability bars
        for i in range(10):
            prob = probabilities[i]
            self.prob_bars[i]['value'] = prob * 100
            self.prob_labels[i].config(text=f"{prob:.1%}")

    def clear_canvas(self):
        """Clear the drawing canvas and reset results."""
        self.canvas.clear()
        self.result_var.set("Draw a digit and click 'Recognize'")
        self.prediction_var.set("?")

        for i in range(10):
            self.prob_bars[i]['value'] = 0
            self.prob_labels[i].config(text="0%")

    def run(self):
        """Start the application."""
        print("\nApplication started! Draw a digit in the window.")
        self.root.mainloop()


def main():
    """Main entry point."""
    print("=" * 50)
    print("Handwritten Digit Recognizer")
    print("=" * 50)

    app = DigitRecognizerApp()
    app.run()


if __name__ == '__main__':
    main()
