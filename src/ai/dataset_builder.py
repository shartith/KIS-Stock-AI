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

    def format_prompt(self, record):
        """학습용 프롬프트 포맷팅 (Input)"""
        try:
            data = json.loads(record.chart_data)
        except:
            data = {}
            
        # 1. ScannerEngine에서 저장한 통합 데이터 구조인 경우
        if "candle_count" in data or "ai_action" in data:
            # data 자체가 분석 결과(Context)임
            symbol = data.get("symbol", record.symbol)
            market = data.get("market", record.market)
            price = data.get("price", 0)
            
            # 캔들 데이터가 내부에 포함되어 있지 않다면 요약 정보라도 활용
            # (ScannerEngine은 analyze_stock 시점에 candle_data를 별도로 가지고 있었으나, 
            #  result 딕셔너리에는 candle_count만 있고 실제 캔들 배열은 없을 수 있음.
            #  만약 없다면, 이 데이터는 학습용으로 부적합할 수 있으니 체크 필요)
            
            # 하지만 놓친 급등주 복기 로직에서는 'result' 전체를 넘겼는데,
            # ScannerEngine의 scan_results에는 캔들 데이터가 포함되지 않는 경우가 많음 (용량 문제로 요약본만 저장)
            
            # 따라서 프롬프트 구성 시 'AI가 분석했던 텍스트'를 재활용하거나
            # 저장 시점에 캔들 데이터를 포함시켜야 함.
            
            # 현재로서는 저장된 ai_reason_detail 등을 활용하여 상황을 재구성
            ai_detail = data.get("ai_reason_detail", "")
            
            prompt = f"""### Instruction:
Analyze the stock and determine the trading action.
Symbol: {symbol} ({market})
Price: {price}

[Context]
The stock showed significant movement today.
AI Analysis Detail: {ai_detail}

### Input:
Based on the market data, what is the correct action?

### Response:
"""
            return prompt

        # 2. 기존 방식 (chart_data가 캔들 딕셔너리인 경우)
        try:
            indicators = json.loads(record.indicators)
        except:
            indicators = {}
            
        candles_summary = ""
        if isinstance(data, dict):
            for tf, candles in data.items():
                if isinstance(candles, list) and len(candles) > 0:
                    latest = candles[-1]
                    candles_summary += f"[{tf}] Close:{latest.get('close', 0)} Vol:{latest.get('volume', 0)}\n"
        
        ta_summary = f"RSI:{indicators.get('rsi', 0):.1f} MACD:{indicators.get('macd', 0):.2f}"
        
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

    def format_completion(self, record):
        """학습용 정답 포맷팅 (Output)"""
        # 놓친 급등주(False Negative) 케이스
        if record.trade_type == "FALSE_NEGATIVE":
            return f"Action: BUY\nReason: {record.ai_reasoning}\nTarget: Strong Uptrend Detected"

        # 일반 매매 기록
        if record.result_type == "WIN":
            action = "BUY"
            reason = record.ai_reasoning
        elif record.result_type == "LOSS":
            action = "AVOID" # 손실난 패턴은 피하도록 학습
            reason = "Technical breakdown observed. Do not enter."
        else:
            action = "HOLD"
            reason = "Wait for clearer signals."

        return f"Action: {action}\nReason: {reason}"

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
