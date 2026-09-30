"""Tạo dữ liệu mẫu để xem thử giao diện khi chưa có API key / chưa học buổi nào.

    backend/.venv/Scripts/python scripts/demo_seed.py --data-dir ./demo-data
    backend/.venv/Scripts/python -m app --data-dir ./demo-data --open

Không chạy lên thư mục dữ liệu thật trừ khi bạn muốn có dữ liệu mẫu trong đó.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import EnvSettings
from app.db.migrations import run_migrations
from app.db.models import (
    ChatMessage,
    Course,
    Flashcard,
    GlossaryTerm,
    LectureSession,
    LlmUsage,
    Pin,
    Segment,
    SegmentMetric,
    Summary,
)
from app.db.session import db_session, make_engine

SEGMENTS = [
    (3, "Buongiorno a tutti, oggi parliamo di regressione logistica.",
     "Chào mọi người, hôm nay chúng ta nói về hồi quy logistic."),
    (9, "La settimana scorsa abbiamo visto la regressione lineare e i suoi limiti.",
     "Tuần trước chúng ta đã xem hồi quy tuyến tính và các hạn chế của nó."),
    (16, "Quando la variabile dipendente è binaria, il modello lineare non funziona bene.",
     "Khi biến phụ thuộc là nhị phân, mô hình tuyến tính không hoạt động tốt."),
    (24, "Per esempio, vogliamo prevedere se uno studente supera l'esame oppure no.",
     "Ví dụ, ta muốn dự đoán một sinh viên có đỗ kỳ thi hay không."),
    (32, "Allora modelliamo la probabilità con la funzione logistica.",
     "Vậy ta mô hình hoá xác suất bằng hàm logistic."),
    (39, "Il logit di p è uguale a beta zero più beta uno x.", "Logit của p bằng β₀ + β₁x."),
    (47, "Il logit è il logaritmo del rapporto tra p e uno meno p, cioè il log degli odds.",
     "Logit là logarit của tỉ số giữa p và 1 − p, tức là log của odds."),
    (56, "Quindi i coefficienti si interpretano in termini di odds ratio.",
     "Do đó các hệ số được diễn giải theo tỉ số odds (odds ratio)."),
    (64, "Se beta uno è positivo, all'aumentare di x aumenta la probabilità di successo.",
     "Nếu β₁ dương, khi x tăng thì xác suất thành công tăng."),
    (72, "I parametri si stimano con il metodo della massima verosimiglianza.",
     "Các tham số được ước lượng bằng phương pháp hợp lý cực đại."),
    (80, "Non esiste una soluzione in forma chiusa, quindi usiamo metodi iterativi.",
     "Không có nghiệm dạng đóng, vì vậy ta dùng các phương pháp lặp."),
    (87, "In R si usa la funzione glm con family uguale binomial.",
     "Trong R dùng hàm glm với family = binomial."),
    (95, "Poi guardiamo la devianza residua e il criterio AIC per confrontare i modelli.",
     "Sau đó ta xem độ lệch (deviance) phần dư và tiêu chí AIC để so sánh các mô hình."),
    (104, "Attenzione all'overfitting quando aggiungete troppe variabili.",
     "Chú ý hiện tượng overfitting khi các bạn thêm quá nhiều biến."),
    (111, "Per valutare il classificatore usiamo la matrice di confusione.",
     "Để đánh giá bộ phân loại, ta dùng ma trận nhầm lẫn."),
    (118, "Da qui calcoliamo sensibilità e specificità.", "Từ đó tính độ nhạy và độ đặc hiệu."),
    (125, "E poi la curva ROC e l'area sotto la curva, l'AUC.",
     "Và sau đó là đường cong ROC và diện tích dưới đường cong, AUC."),
    (133, "Un AUC di zero virgola cinque corrisponde a un classificatore casuale.",
     "AUC bằng 0,5 tương ứng với một bộ phân loại ngẫu nhiên."),
    (141, "Ci sono domande fino a qui?", "Đến đây có câu hỏi nào không?"),
    (146, "Sì.", "Có."),
    (149, "Prego, dimmi.", "Mời em, em nói đi."),
    (156, "Per la prossima settimana, fate gli esercizi del capitolo quattro.",
     "Tuần tới, các em làm bài tập chương bốn."),
    (164, "Il progetto va consegnato entro venerdì venti ottobre sulla piattaforma.",
     "Đồ án phải nộp trước thứ Sáu ngày 20 tháng 10 trên nền tảng học tập."),
    (173, "L'esame scritto sarà a dicembre, vi manderò i dettagli per email.",
     "Bài thi viết sẽ vào tháng 12, cô sẽ gửi chi tiết qua email."),
    (181, "Bene, facciamo una pausa di dieci minuti.", "Được rồi, chúng ta nghỉ giải lao mười phút."),
]

SUMMARY = r"""## Tổng quan buổi học
Buổi học giới thiệu **hồi quy logistic** (*regressione logistica*, logistic regression) cho biến phụ thuộc nhị phân: vì sao hồi quy tuyến tính không phù hợp, cách mô hình hoá xác suất bằng hàm logistic, diễn giải hệ số qua *odds ratio*, ước lượng bằng hợp lý cực đại và đánh giá bộ phân loại bằng ma trận nhầm lẫn, ROC/AUC.

## Ý chính
- [00:16] Khi biến phụ thuộc là nhị phân, mô hình tuyến tính cho dự đoán ngoài khoảng [0, 1].
- [00:39] Mô hình: logit của xác suất là hàm tuyến tính của biến giải thích.
- [00:56] Hệ số được diễn giải qua *odds ratio*: $e^{\beta_1}$ là hệ số nhân của odds khi $x$ tăng 1 đơn vị.
- [01:12] Tham số ước lượng bằng **hợp lý cực đại** (*massima verosimiglianza*), không có nghiệm dạng đóng.
- [01:27] Trong R: `glm(y ~ x, family = binomial)`.
- [01:51] Đánh giá: ma trận nhầm lẫn → độ nhạy, độ đặc hiệu; đường ROC và AUC.

## Khái niệm & định nghĩa
| IT | VI | EN | Giải thích |
|---|---|---|---|
| regressione logistica | hồi quy logistic | logistic regression | Mô hình xác suất cho biến nhị phân |
| odds ratio | tỉ số odds | odds ratio | Tỉ số giữa odds của hai nhóm |
| massima verosimiglianza | hợp lý cực đại | maximum likelihood | Chọn tham số làm dữ liệu quan sát "dễ xảy ra" nhất |
| devianza residua | độ lệch phần dư | residual deviance | Thước đo mức khớp của mô hình |
| matrice di confusione | ma trận nhầm lẫn | confusion matrix | Bảng so sánh dự đoán và thực tế |

## Công thức
$$\operatorname{logit}(p) = \ln\frac{p}{1-p} = \beta_0 + \beta_1 x$$

$$p = \frac{1}{1 + e^{-(\beta_0 + \beta_1 x)}}$$

Trong đó $p$ là xác suất thành công, $\beta_0$ là hệ số chặn, $\beta_1$ là hệ số của $x$.

## Bài tập / Deadline / Thông báo hành chính
- [02:36] Làm bài tập chương 4 cho tuần sau.
- [02:44] **Nộp đồ án**: "entro venerdì venti ottobre" → *trước thứ Sáu 20/10*, nộp trên nền tảng học tập.
- [02:53] Thi viết vào **tháng 12**; chi tiết sẽ gửi qua email.

## Những đoạn cần xem lại
- [00:47] 📌 (chưa hiểu) Định nghĩa logit là log của odds.
- [01:35] Nhận dạng không chắc chắn: đoạn về devianza residua và AIC.

## Câu hỏi gợi ý để hỏi giảng viên
1. Cô có thể cho ví dụ số về cách diễn giải odds ratio không?
   *Professoressa, potrebbe fare un esempio numerico di interpretazione dell'odds ratio?*
2. Đồ án làm theo nhóm hay cá nhân?
   *Professoressa, il progetto è individuale o di gruppo?*
"""

CHAT = [
    ("user", "Giảng viên định nghĩa logit thế nào?"),
    ("assistant",
     "Theo bài giảng, **logit** (*logit*, logit) của xác suất $p$ là logarit của odds [00:47]:\n\n"
     "$$\\operatorname{logit}(p) = \\ln\\frac{p}{1-p}$$\n\n"
     "Giảng viên đặt logit bằng một hàm tuyến tính của $x$: $\\operatorname{logit}(p) = \\beta_0 + \\beta_1 x$ [00:39]. "
     "Nhờ đó $e^{\\beta_1}$ được hiểu là *odds ratio* khi $x$ tăng 1 đơn vị [00:56].\n\n"
     "**Kiến thức bổ sung**: đảo ngược công thức ta được hàm sigmoid $p = 1/(1+e^{-(\\beta_0+\\beta_1 x)})$, "
     "luôn nằm trong khoảng (0, 1)."),
]

COMPOSE = {
    "question_it": "Professoressa, potrebbe spiegare di nuovo come si interpreta l'odds ratio? Grazie mille.",
    "back_translation_vi": "Thưa cô, cô có thể giải thích lại cách diễn giải tỉ số odds không ạ? Cảm ơn cô rất nhiều.",
    "notes": "Dùng “Professoressa” vì giảng viên là nữ (Prof. Maria Rossi). Nên hỏi vào cuối buổi hoặc qua chat riêng.",
}

CARDS = [
    ("la verosimiglianza", "hợp lý (likelihood)", "danh từ", 9),
    ("stimare", "ước lượng", "động từ", 9),
    ("la variabile dipendente", "biến phụ thuộc", "cụm danh từ", 2),
    ("prevedere", "dự đoán", "động từ", 3),
    ("superare l'esame", "thi đỗ", "cụm động từ", 3),
    ("consegnare", "nộp (bài)", "động từ", 22),
    ("entro", "trước (thời hạn)", "giới từ", 22),
    ("il rapporto", "tỉ số; mối quan hệ", "danh từ", 6),
    ("la matrice di confusione", "ma trận nhầm lẫn", "cụm danh từ", 14),
    ("la sensibilità", "độ nhạy", "danh từ", 15),
    ("fare una pausa", "nghỉ giải lao", "cụm động từ", 24),
    ("il capitolo", "chương (sách)", "danh từ", 21),
]

GLOSSARY = [
    ("regressione logistica", "hồi quy logistic", "logistic regression"),
    ("massima verosimiglianza", "hợp lý cực đại", "maximum likelihood"),
    ("odds ratio", "tỉ số odds", "odds ratio"),
    ("matrice di confusione", "ma trận nhầm lẫn", "confusion matrix"),
    ("variabile dipendente", "biến phụ thuộc", "dependent variable"),
    ("devianza", "độ lệch (deviance)", "deviance"),
]


def seed(data_dir: Path) -> None:
    env = EnvSettings(data_dir=data_dir)
    engine = make_engine(env.db_path)
    run_migrations(engine)
    rnd = random.Random(7)
    now = datetime.now(UTC)
    with db_session(engine) as s:
        course = Course(name="Thống kê đa biến", name_it="Statistica Multivariata", lecturer="Prof. Maria Rossi")
        s.add(course)
        s.commit()
        s.refresh(course)
        for it, vi, en in GLOSSARY:
            s.add(GlossaryTerm(course_id=course.id, term_it=it, term_vi=vi, term_en=en))

        ended = LectureSession(
            course_id=course.id,
            title="Lezione 3 – Regressione logistica",
            started_at=now - timedelta(days=1, hours=3),
            ended_at=now - timedelta(days=1, hours=1),
            status="ended",
            active_ms=190_000,
            post_status="done",
        )
        s.add(ended)
        s.commit()
        s.refresh(ended)
        seg_ids = []
        for i, (sec, it, vi) in enumerate(SEGMENTS):
            seg = Segment(
                session_id=ended.id,
                seq=i + 1,
                t_start_ms=sec * 1000,
                t_end_ms=sec * 1000 + min(7000, 900 + len(it) * 70),
                text_it=it,
                text_vi=vi,
                confidence=0.42 if i == 12 else round(rnd.uniform(0.72, 0.95), 3),
                status="translated",
            )
            s.add(seg)
            s.commit()
            s.refresh(seg)
            seg_ids.append(seg.id)
            s.add(SegmentMetric(segment_id=seg.id, session_id=ended.id, audio_ms=seg.t_end_ms - seg.t_start_ms,
                                stt_ms=rnd.randint(450, 900), it_latency_ms=rnd.randint(1500, 2600),
                                vi_latency_ms=rnd.randint(3000, 4800)))
        s.add(Pin(session_id=ended.id, segment_id=seg_ids[6], kind="confused", note="Hỏi lại cô về odds"))
        s.add(Pin(session_id=ended.id, segment_id=seg_ids[22], kind="todo", note="Deadline đồ án"))
        s.add(Summary(session_id=ended.id, content_md=SUMMARY, model="claude-sonnet-5"))
        for role, content in CHAT:
            s.add(ChatMessage(session_id=ended.id, role=role, content=content, mode="qa",
                              model="claude-sonnet-5" if role == "assistant" else None))
        s.add(ChatMessage(session_id=ended.id, role="user", mode="compose_it",
                          content="Thầy có thể giải thích lại cách diễn giải odds ratio không ạ?"))
        s.add(ChatMessage(session_id=ended.id, role="assistant", mode="compose_it", model="claude-sonnet-5",
                          content=json.dumps(COMPOSE, ensure_ascii=False)))
        for front, back, pos, idx in CARDS:
            s.add(Flashcard(session_id=ended.id, front_it=front, back_vi=back, pos=pos,
                            example_it=SEGMENTS[idx][1], example_vi=SEGMENTS[idx][2], due_at=now))
        for _ in SEGMENTS:
            s.add(LlmUsage(session_id=ended.id, purpose="translate", model="claude-haiku-4-5-20251001",
                           input_tokens=rnd.randint(520, 700), output_tokens=rnd.randint(30, 70)))
        s.add(LlmUsage(session_id=ended.id, purpose="chat", model="claude-sonnet-5", input_tokens=900,
                       output_tokens=420, cache_read_tokens=0, cache_write_tokens=2600, cache_write_1h_tokens=2600))
        s.add(LlmUsage(session_id=ended.id, purpose="summary", model="claude-sonnet-5", input_tokens=3900,
                       output_tokens=1600))
        s.add(LlmUsage(session_id=ended.id, purpose="flashcard", model="claude-sonnet-5", input_tokens=2100,
                       output_tokens=1300))

        paused = LectureSession(course_id=course.id, title="Lezione 4 – Alberi di decisione",
                                started_at=now - timedelta(minutes=20), status="paused", active_ms=42_000)
        s.add(paused)
        s.commit()
        s.refresh(paused)
        for i, (sec, it, vi) in enumerate([
            (2, "Oggi introduciamo gli alberi di decisione.", "Hôm nay chúng ta giới thiệu cây quyết định."),
            (8, "Ogni nodo divide i dati secondo una variabile.", "Mỗi nút chia dữ liệu theo một biến."),
            (15, "Per scegliere la divisione usiamo l'indice di Gini o l'entropia.",
             "Để chọn cách chia, ta dùng chỉ số Gini hoặc entropy."),
            (24, "Un albero troppo profondo tende all'overfitting, quindi facciamo la potatura.", None),
        ]):
            s.add(Segment(session_id=paused.id, seq=i + 1, t_start_ms=sec * 1000, t_end_ms=sec * 1000 + 5200,
                          text_it=it, text_vi=vi, status="translated" if vi else "transcribed", confidence=0.9))
        s.commit()

    # Chi phí phiên = tổng llm_usage × bảng giá mặc định
    from app.services.cost import UsageRecorder
    from app.services.settings_store import SettingsStore

    UsageRecorder(engine, SettingsStore(engine)).refresh_session(ended.id)
    print(f"Đã tạo dữ liệu mẫu trong {data_dir}")


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, type=Path)
    seed(parser.parse_args().data_dir)
