"""
Train Local Model - 로컬 LLM 미세조정 (Fine-tuning)
Unsloth를 사용하여 Qwen/Llama 모델을 학습시키고, GGUF로 변환하여 Ollama에 등록합니다.
"""
import os
import torch
import subprocess
from dataset_builder import DatasetBuilder
from transformers import TrainingArguments
from trl import SFTTrainer

# Unsloth 라이브러리 (필수)
try:
    from unsloth import FastLanguageModel
    HAS_UNSLOTH = True
except ImportError:
    HAS_UNSLOTH = False
    print("⚠️ Unsloth not found. Please install it for efficient training.")

def run_command(cmd):
    """쉘 명령어 실행"""
    print(f"Executing: {cmd}")
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.communicate()
    if process.returncode != 0:
        print(f"Error: {err.decode('utf-8')}")
        raise Exception(f"Command failed: {cmd}")
    return out.decode('utf-8')

def train_and_register_ollama(base_model_name = "unsloth/Qwen2.5-7B-Instruct-bnb-4bit", 
                              new_model_name = "qwen-stock-trader"):
    print(f"🚀 Starting training pipeline for {base_model_name}...")
    
    if not HAS_UNSLOTH:
        raise ImportError("Unsloth library is required for this pipeline. (pip install unsloth)")

    # 1. 데이터셋 준비
    builder = DatasetBuilder()
    data_files = builder.get_all_data_files()
    
    from datasets import load_dataset
    # 여러 파일을 하나의 데이터셋으로 로드
    dataset = load_dataset("json", data_files=data_files, split="train")
    print(f"📚 Loaded {len(dataset)} training examples from {len(data_files)} files")

    # 2. 모델 로드 (Qwen 2.5)
    max_seq_length = 2048
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name = base_model_name,
        max_seq_length = max_seq_length,
        dtype = None,
        load_in_4bit = True,
    )
    
    # LoRA 어댑터 추가
    model = FastLanguageModel.get_peft_model(
        model,
        r = 16,
        target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                          "gate_proj", "up_proj", "down_proj",],
        lora_alpha = 16,
        lora_dropout = 0,
        bias = "none",
        use_gradient_checkpointing = True,
    )

    # 3. 프롬프트 포맷팅 (Qwen ChatML 스타일)
    # Qwen은 ChatML 포맷을 사용하므로 이에 맞춰야 함
    def formatting_prompts_func(examples):
        instructions = examples["instruction"]
        outputs = examples["output"]
        texts = []
        for instruction, output in zip(instructions, outputs):
            # ChatML Format
            text = f"<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
            texts.append(text)
        return {"text": texts}

    # 4. 학습 설정
    trainer = SFTTrainer(
        model = model,
        tokenizer = tokenizer,
        train_dataset = dataset,
        dataset_text_field = "text",
        max_seq_length = max_seq_length,
        dataset_num_proc = 2,
        formatting_func = formatting_prompts_func,
        args = TrainingArguments(
            per_device_train_batch_size = 2,
            gradient_accumulation_steps = 4,
            warmup_steps = 5,
            max_steps = 60, # 데이터 양에 따라 조정 필요
            learning_rate = 2e-4,
            fp16 = not torch.cuda.is_bf16_supported(),
            bf16 = torch.cuda.is_bf16_supported(),
            logging_steps = 1,
            optim = "adamw_8bit",
            weight_decay = 0.01,
            lr_scheduler_type = "linear",
            seed = 3407,
            output_dir = "outputs",
        ),
    )

    # 5. 학습 실행
    print("🔥 Training started...")
    trainer.train()

    # 6. GGUF 변환 및 저장 (Ollama용)
    print("💾 Converting to GGUF format...")
    # unsloth는 내부적으로 llama.cpp 변환 기능을 제공함
    model.save_pretrained_gguf("model_gguf", tokenizer, quantization_method = "q4_k_m")
    
    # 7. Ollama 모델 생성
    print(f"🐳 Creating Ollama model: {new_model_name}...")
    
    modelfile_content = f"""
FROM ./model_gguf/{base_model_name.split('/')[-1]}-Q4_K_M.gguf
TEMPLATE \"\"\"{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
\"\"\"
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
"""
    with open("Modelfile", "w") as f:
        f.write(modelfile_content)

    try:
        run_command(f"ollama create {new_model_name} -f Modelfile")
        print(f"✅ Ollama model '{new_model_name}' created successfully!")
    except Exception as e:
        print(f"⚠️ Failed to create Ollama model: {e}")
        print("You can manually create it using: ollama create qwen-stock-trader -f Modelfile")

if __name__ == "__main__":
    try:
        # 베이스 모델을 Qwen2.5로 변경
        train(base_model_name="unsloth/Qwen2.5-7B-Instruct-bnb-4bit")
    except Exception as e:
        print(f"❌ Training failed: {e}")
