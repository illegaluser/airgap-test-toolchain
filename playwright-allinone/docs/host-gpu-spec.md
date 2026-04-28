# 호스트 GPU 사양 (Phase 0 T0.2 산출물)

> Phase 1.5 후보 모델 표의 "현재 호스트 적합 여부" 컬럼 산정 근거.

## 개발 호스트 (현재)

| 항목 | 값 | 측정 명령 |
| --- | --- | --- |
| Platform | macOS 26.4.1 (Apple Silicon) | `sw_vers` |
| Chip | Apple M4 Pro | `system_profiler SPHardwareDataType` |
| 통합 메모리 | 48 GB | 동일 |
| Discrete GPU | 없음 (통합 GPU) | — |
| CUDA | 미지원 (Metal Performance Shaders 만) | — |

> **주의**: 개발 호스트는 컨테이너 내 Ollama 가 CPU 추론으로 동작. 모델 후보 선정의 근거 호스트가 **아님**.

## 운영 호스트 (TBD — 운영자 입력 필요)

운영 호스트에서 다음 명령을 실행한 결과를 기록한다.

```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
df -h /var/lib/ollama  # 모델 저장 경로
free -h | head -2
```

| 항목 | 값 |
| --- | --- |
| GPU 모델 | (예: NVIDIA RTX 4090 24GB) |
| 총 VRAM | (예: 24576 MiB) |
| 가용 VRAM (Brain Planner + Healer + Test Planning Brain 동시 부하 후) | (예: 17000 MiB) |
| CUDA 드라이버 버전 | (예: 535.x) |
| 디스크 여유 (`/var/lib/ollama`) | (예: 200 GB) |
| 시스템 메모리 | (예: 64 GB) |

## Phase 1.5 후보 모델 적합 여부 (운영 호스트 VRAM 기준)

운영자가 위 표를 채운 후 Phase 1.5 §"T1.5.3 후보 모델 벤치마크" 의 "현재 호스트 적합 여부" 컬럼을 다음 룰로 채운다.

| 모델 | Q4 VRAM 요구 | 적합 룰 |
| --- | --- | --- |
| `gemma4:26b` | ~17GB | 가용 VRAM ≥ 17000 MiB |
| `qwen2.5:32b` | ~22GB | 가용 VRAM ≥ 22000 MiB |
| `llama3.3:70b-q4_K_M` | ~42GB | 가용 VRAM ≥ 42000 MiB |
| `granite3-dense:8b` | ~6GB | 가용 VRAM ≥ 6000 MiB |

가용 VRAM 미달 모델은 Phase 1.5 벤치마크에서 자동 제외.

## 변경 이력

| 날짜 | 변경 |
| --- | --- |
| 2026-04-28 | 초기 작성. 개발 호스트(M4 Pro) 기록. 운영 호스트는 운영자 입력 대기 |
