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

    def fetch_raw_data(self, min_profit_rate: float = 0.5):
        """DB에서 유의미한(수익이 난) 매매 데이터 조회"""
        session = self.db.get_session()
        try:
            # 익절했거나 손절 방어에 성공한 케이스 위주로 학습 (필터링 조건은 조정 가능)
            # 여기서는 모든 데이터를 가져와서 라벨링(Good/Bad)하는 방식을 사용
            results = session.query(TrainingDataset).all()
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

    def get_all_data_files(self) -> list:
        """datasets 폴더 내의 모든 jsonl 파일 경로 반환 (DB 추출본 포함)"""
        # 1. DB 최신 데이터 추출
        self.build_jsonl(filename="db_latest.jsonl")
        
        files = []
        for f in os.listdir(self.output_dir):
            if f.endswith(".jsonl"):
                files.append(os.path.join(self.output_dir, f))
        return files

    def build_jsonl(self, filename="train_data.jsonl"):
        """DB 데이터를 JSONL로 변환 (기본)"""
        data = self.fetch_raw_data()
        output_path = os.path.join(self.output_dir, filename)
        
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for record in data:
                if not record.chart_data: continue
                
                entry = {
                    "instruction": self.format_prompt(record),
                    "output": self.format_completion(record)
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                count += 1
                
        print(f"✅ DB Dataset exported: {output_path} ({count} records)")
        return output_path

if __name__ == "__main__":
    builder = DatasetBuilder()
    builder.build_jsonl()
