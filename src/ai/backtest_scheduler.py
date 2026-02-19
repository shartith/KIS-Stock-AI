"""
Backtest Scheduler - 백테스트 자동 실행 스케줄러

시점:
- 매일 16:00 — 당일 AI 판단 vs 실제 결과 비교
- 매주 일요일 — 전략별 주간 성과 리포트
- 설정 변경 시 — 변경된 전략으로 최근 30일 백테스트
"""
import json
import schedule
import time
from datetime import datetime, timedelta
from typing import List, Dict

from backtest_engine import BacktestEngine, BacktestConfig
from config import TOP_STOCKS
from database import DatabaseManager
from notification import NotificationService


class BacktestScheduler:
    """백테스트 자동 실행 스케줄러"""
    
    def __init__(self):
        self.engine = BacktestEngine()
        self.db = DatabaseManager()
        self.notification = NotificationService()
    
    def run_daily_validation(self):
        """매일 장 마감 후 — 상위 5개 종목에 대해 최근 30일 백테스트"""
        print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M')}] 일간 백테스트 검증 시작")
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        results = []
        for symbol, name in TOP_STOCKS[:5]:
            config = BacktestConfig(
                symbol=symbol,
                name=name,
                start_date=start_date,
                end_date=end_date,
                strategy="ai_combined"
            )
            result = self.engine.run(config)
            
            if not result.error:
                self.db.save_backtest(config, result)
                results.append({
                    "name": name,
                    "return": result.metrics.get("total_return", 0),
                    "win_rate": result.metrics.get("win_rate", 0),
                    "trades": result.metrics.get("total_trades", 0)
                })
        
        # Discord 알림
        if results:
            report_lines = [f"📊 **일간 백테스트 검증** ({end_date})"]
            for r in results:
                emoji = "📈" if r["return"] > 0 else "📉"
                report_lines.append(
                    f"{emoji} {r['name']}: {r['return']:+.1f}% (승률 {r['win_rate']:.0f}%, {r['trades']}거래)"
                )
            self.notification.send_message("\n".join(report_lines))
        
        print(f"  ✅ 일간 검증 완료: {len(results)}개 종목")
    
    def run_weekly_report(self):
        """매주 일요일 — 전략별 주간 성과 비교"""
        print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M')}] 주간 전략 리포트 시작")
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        
        strategies = ["ai_combined", "technical", "momentum", "volume", "value"]
        test_symbol = TOP_STOCKS[0][0]  # 삼성전자
        test_name = TOP_STOCKS[0][1]
        
        strategy_results = []
        for strategy in strategies:
            config = BacktestConfig(
                symbol=test_symbol,
                name=test_name,
                start_date=start_date,
                end_date=end_date,
                strategy=strategy
            )
            result = self.engine.run(config)
            
            if not result.error:
                self.db.save_backtest(config, result)
                strategy_results.append({
                    "strategy": strategy,
                    "return": result.metrics.get("total_return", 0),
                    "mdd": result.metrics.get("mdd", 0),
                    "sharpe": result.metrics.get("sharpe_ratio", 0),
                    "win_rate": result.metrics.get("win_rate", 0)
                })
        
        # Discord 알림
        if strategy_results:
            report_lines = [
                f"📋 **주간 전략 비교 리포트** ({test_name})",
                f"기간: {start_date} ~ {end_date}",
                ""
            ]
            strategy_results.sort(key=lambda x: x["return"], reverse=True)
            for i, r in enumerate(strategy_results):
                medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i]
                report_lines.append(
                    f"{medal} **{r['strategy']}**: {r['return']:+.1f}% "
                    f"(MDD -{r['mdd']:.1f}%, 샤프 {r['sharpe']:.2f})"
                )
            
            self.notification.send_message("\n".join(report_lines))
        
        print(f"  ✅ 주간 리포트 완료: {len(strategy_results)}개 전략")
    
    def run_on_config_change(self, changed_keys: list = None):
        """설정 변경 시 — 최근 30일 백테스트로 검증"""
        print(f"\n⏰ 설정 변경 감지, 백테스트 검증 실행")
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        config = BacktestConfig(
            symbol=TOP_STOCKS[0][0],
            name=TOP_STOCKS[0][1],
            start_date=start_date,
            end_date=end_date,
            strategy="ai_combined"
        )
        
        result = self.engine.run(config)
        if not result.error:
            self.db.save_backtest(config, result)
            print(f"  ✅ 검증 완료: {result.metrics.get('total_return', 0):+.1f}%")
        
        return result
    
    def start(self):
        """스케줄러 시작"""
        print("📅 백테스트 스케줄러 시작")
        
        # 매일 16:00 (장 마감 후)
        schedule.every().day.at("16:00").do(self.run_daily_validation)
        
        # 매주 일요일 10:00
        schedule.every().sunday.at("10:00").do(self.run_weekly_report)
        
        print("  📌 일간 검증: 매일 16:00")
        print("  📌 주간 리포트: 매주 일요일 10:00")
        
        while True:
            schedule.run_pending()
            time.sleep(60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daemon", "daily", "weekly"], default="daemon")
    args = parser.parse_args()
    
    scheduler = BacktestScheduler()
    
    if args.mode == "daily":
        scheduler.run_daily_validation()
    elif args.mode == "weekly":
        scheduler.run_weekly_report()
    else:
        scheduler.start()
