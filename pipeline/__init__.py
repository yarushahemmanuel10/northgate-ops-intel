"""Northgate operational intelligence platform: pipeline code (Layers 1-2).

The pipeline only reads data/raw/ and knowledge_base/ (never the generator's clean data,
ground truth or config), so every score measured against the answer key is honest (D-017).
"""
