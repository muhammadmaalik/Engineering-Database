#!/usr/bin/env python3
"""
Motherbrain Training Pipeline - Unsloth LoRA fine-tuning
"""

import json
import sys
import os
import torch
from pathlib import Path
from datetime import datetime
from unsloth import FastLanguageModel

VAULT_ROOT = Path.home() / ".motherbrain" / "vault"
MODELS_DIR = VAULT_ROOT / "shared" / "base_models"
ADAPTERS_DIR = VAULT_ROOT / "shared" / "adapters"


def load_dataset(jsonl_path):
    conversations = []
    with open(jsonl_path) as f:
        for line in f:
            record = json.loads(line)
            if record["label"] == "corrected" and "correction" in record:
                conversations.append({
                    "instruction": record["payload"],
                    "output": record["correction"]
                })
            elif record["label"] == "good":
                conversations.append({
                    "instruction": record["payload"],
                    "output": record["payload"]
                })
    return conversations


def train(model_id, dataset_path, output_name=None):
    model_path = MODELS_DIR / f"{model_id}.gguf"
    if not model_path.exists():
        print(f"[TRAIN] Model not found: {model_path}")
        return None

    print(f"[TRAIN] Loading dataset: {dataset_path}")
    conversations = load_dataset(dataset_path)

    if len(conversations) == 0:
        print("[TRAIN] No valid training examples found.")
        return None

    print(f"[TRAIN] Training examples: {len(conversations)}")

    # Format as instruction-output pairs
    train_data = []
    for conv in conversations:
        train_data.append({
            "instruction": conv["instruction"],
            "output": conv["output"]
        })
    
    print("[TRAIN] Loading model (this may take a few minutes)...")
    
    # Unsloth can load from a local file path with the model_name parameter
    # Use the directory containing the gguf as a local model
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/gemma-2-9b-it-bnb-4bit",
        max_seq_length=2048,
        load_in_4bit=True,
        dtype=None,
    )

    print("[TRAIN] Adding LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Format prompts
    def format_prompt(examples):
        texts = []
        for instruction, output in zip(examples["instruction"], examples["output"]):
            text = f"<start_of_turn>user\n{instruction}<end_of_turn>\n<start_of_turn>model\n{output}<end_of_turn>"
            texts.append(text)
        return {"text": texts}

    from datasets import Dataset
    dataset = Dataset.from_list(train_data)
    dataset = dataset.map(format_prompt, batched=True)

    from trl import SFTTrainer
    from transformers import TrainingArguments

    print("[TRAIN] Starting training...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        args=TrainingArguments(
            output_dir="./training_output",
            num_train_epochs=3,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            learning_rate=2e-4,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=42,
            report_to="none",
        ),
    )

    trainer.train()

    ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    if not output_name:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"{model_id}_lora_{ts}"

    adapter_path = ADAPTERS_DIR / output_name
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))

    print(f"\n[TRAIN] Adapter saved to: {adapter_path}")
    return str(adapter_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python train.py <model_id> <dataset.jsonl> [output_name]")
        print("Example: python train.py gemma-2-9b-it-Q5_K_M dataset.jsonl my_adapter")
        sys.exit(1)

    model_id = sys.argv[1]
    dataset_path = sys.argv[2]
    output_name = sys.argv[3] if len(sys.argv) > 3 else None
    train(model_id, dataset_path, output_name)
