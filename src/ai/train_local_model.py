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
    data_files, processed_ids = builder.get_all_data_files(new_only=True)
    
    if not data_files:
        print("⚠️ No data files found. Skipping training.")
        return

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
    
    # 7. Ollama 모델 생성 (버전 관리)
    from datetime import datetime
    version_tag = datetime.now().strftime("%Y%m%d")
    versioned_model_name = f"{new_model_name}:{version_tag}"
    latest_model_name = f"{new_model_name}:latest"
    
    print(f"🐳 Creating Ollama model: {versioned_model_name}...")
    
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
        # 버전별 모델 생성
        run_command(f"ollama create {versioned_model_name} -f Modelfile")
        print(f"✅ Created versioned model: {versioned_model_name}")
        
        # latest 태그 갱신 (복사)
        run_command(f"ollama cp {versioned_model_name} {latest_model_name}")
        print(f"✅ Updated latest model: {latest_model_name}")
        
        # 구버전 정리 (최신 3개만 유지)
        manage_old_models(new_model_name)
        
        # 8. 학습 완료된 데이터 마킹 (재학습 방지)
        if processed_ids:
            builder.mark_processed(processed_ids)
            print(f"✅ Marked {len(processed_ids)} records as trained.")
            
    except Exception as e:
        print(f"⚠️ Failed to create Ollama model: {e}")

def manage_old_models(base_name="qwen-stock-trader", keep_count=3):
    """오래된 Ollama 모델 버전 삭제"""
    try:
        # 모델 목록 조회
        output = run_command("ollama list")
        lines = output.strip().split('\n')[1:] # 헤더 제외
        
        # 해당 베이스 이름을 가진 모델 필터링 (latest 제외)
        versions = []
        for line in lines:
            parts = line.split()
            if not parts: continue
            name = parts[0]
            if name.startswith(f"{base_name}:") and not name.endswith(":latest"):
                versions.append(name)
        
        # 이름순 정렬 (날짜 태그이므로 문자열 정렬 = 날짜 정렬)
        versions.sort(reverse=True) # 최신순
        
        # keep_count 초과분 삭제
        if len(versions) > keep_count:
            to_delete = versions[keep_count:]
            for model in to_delete:
                print(f"🗑️ Deleting old model: {model}")
                run_command(f"ollama rm {model}")
                
    except Exception as e:
        print(f"⚠️ Failed to cleanup old models: {e}")

if __name__ == "__main__":
    try:
        # 베이스 모델을 Qwen2.5로 변경
        train_and_register_ollama(base_model_name="unsloth/Qwen2.5-7B-Instruct-bnb-4bit")
    except Exception as e:
        print(f"❌ Training failed: {e}")
