"""
Dataset Builder - 학습 데이터 전처리 및 변환
DB에 저장된 매매 기록을 LLM 학습용 데이터셋(JSONL)으로 변환합니다.
"""
import json
import os
import pandas as pd
from database import DatabaseManager, TrainingDataset

class DatasetBuilder:
    def __init__(self):
        self.db = DatabaseManager()
        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")
        os.makedirs(self.output_dir, exist_ok=True)

    def fetch_raw_data(self, min_profit_rate: float = 0.5, new_only: bool = True):
        """DB에서 유의미한(수익이 난) 매매 데이터 조회"""
        session = self.db.get_session()
        try:
            query = session.query(TrainingDataset)
            if new_only:
                query = query.filter(TrainingDataset.is_trained == 0)
                
            results = query.all()
            return results
        finally:
            session.close()

    def format_prompt(self, record: TrainingDataset) -> str:
        """학습용 프롬프트 포맷팅 (Input)"""
        chart_data = json.loads(record.chart_data)
        indicators = json.loads(record.indicators)
        
        # 캔들 데이터 요약 (최근 5개)
        candles_summary = ""
        for tf, data in chart_data.items():
            if data and len(data) > 0:
                latest = data[-1]
                candles_summary += f"[{tf}] Close:{latest.get('close')} Vol:{latest.get('volume')}\n"
        
        # 지표 요약
        ta_summary = f"RSI:{indicators.get('rsi', 0):.1f} MACD:{indicators.get('macd', 0):.2f} Trend:{indicators.get('trend', 'neutral')}"
        
        prompt = f"""### Instruction:
Analyze the following stock data and decide whether to BUY, HOLD, or SELL.
Symbol: {record.symbol} ({record.market})

[Chart Data]
{candles_summary}

[Technical Indicators]
{ta_summary}

### Input:
Provide a trading decision and reasoning.

### Response:
"""
        return prompt

    def format_completion(self, record: TrainingDataset) -> str:
        """학습용 정답 포맷팅 (Output)"""
        # 실제 매매 결과에 따라 AI의 판단을 '재구성'
        # 수익이 났으면 -> "BUY" 권장 및 당시 AI의 논리 사용
        # 손실이 났으면 -> "HOLD" 또는 "AVOID" 권장 (실패한 매수는 따라하지 않도록)
        
        if record.result_type == "WIN" and record.profit_rate >= 0.5:
            action = "BUY"
            reason = record.ai_reasoning # 당시 성공했던 논리
        elif record.result_type == "LOSS":
            action = "AVOID"
            reason = "Current technical indicators suggest a downtrend risk. Avoid entry."
        else:
            action = "HOLD"
            reason = "Market signals are ambiguous. Wait for clearer confirmation."

        return f"Action: {action}\nReason: {reason}\nTarget Profit: {record.profit_rate:.1f}%"

    def get_all_data_files(self, new_only: bool = True) -> tuple:
        """datasets 폴더 내의 모든 jsonl 파일 경로 반환 (DB 추출본 포함)"""
        # 1. DB 최신 데이터 추출
        path, ids = self.build_jsonl(filename="db_latest.jsonl", new_only=new_only)
        
        files = []
        if path:
            files.append(path)
            
        for f in os.listdir(self.output_dir):
            if f.endswith(".jsonl") and f != "db_latest.jsonl":
                files.append(os.path.join(self.output_dir, f))
        return files, ids

    def build_jsonl(self, filename="train_data.jsonl", new_only: bool = True):
        """DB 데이터를 JSONL로 변환 (기본)"""
        data = self.fetch_raw_data(new_only=new_only)
        if not data:
            print("⚠️ No new training data found.")
            return None, []
            
        output_path = os.path.join(self.output_dir, filename)
        processed_ids = []
        
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for record in data:
                if not record.chart_data: continue
                
                entry = {
                    "instruction": self.format_prompt(record),
                    "output": self.format_completion(record)
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                processed_ids.append(record.id)
                count += 1
                
        print(f"✅ DB Dataset exported: {output_path} ({count} records)")
        return output_path, processed_ids

    def mark_processed(self, ids: list):
        """처리된 데이터 학습 완료 표시"""
        self.db.mark_data_as_trained(ids)

if __name__ == "__main__":
    builder = DatasetBuilder()
    path, ids = builder.build_jsonl()
    # builder.mark_processed(ids) # Uncomment to mark as trained
