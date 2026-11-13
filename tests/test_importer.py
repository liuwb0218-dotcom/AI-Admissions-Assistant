"""采集层测试：幂等导入、非法行拦截、真实数据清洗规则。"""


def test_duplicate_import_is_skipped():
    from collector.importer import import_all
    from database.connection import SessionLocal

    db = SessionLocal()
    try:
        summary = import_all(db, include_neu=False)
        db.commit()
    finally:
        db.close()

    assert summary["knowledge_docs"]["inserted"] == 0
    assert summary["knowledge_docs"]["skipped"] > 0


def test_invalid_row_is_rejected():
    from collector.importer import import_knowledge
    from database.connection import SessionLocal

    db = SessionLocal()
    try:
        result = import_knowledge(
            db,
            [
                {"category": "unknown", "title": "非法分类", "content": "这段内容长度足够但没有合法分类"},
                {"category": "faq", "title": "正文过短", "content": "太短"},
            ],
        )
        db.rollback()
    finally:
        db.close()

    assert result.inserted == 0
    assert result.invalid == 2


def test_normalize_major_unifies_fullwidth_brackets():
    from collector.nenu_importer import normalize_major

    assert normalize_major("会计学（中外合作办学）") == normalize_major("会计学(中外合作办学)")
    assert normalize_major("  数据科学　 ") == "数据科学"


def test_score_rank_zero_is_treated_as_unpublished():
    from collector.nenu_importer import clean_score_rows

    rows, _ = clean_score_rows(
        [
            {
                "year": "2025",
                "province": "西藏",
                "type": "一般计划（普通类）",
                "major_type": "理工",
                "major": "计算机类",
                "major_attr": "普通类",
                "min_admission_score": "517",
                "min_rank": "0",
            }
        ]
    )

    assert rows[0]["min_score"] == 517
    assert rows[0]["min_rank"] is None


def test_duplicate_major_attr_is_canonicalized():
    from collector.nenu_importer import clean_plan_rows

    rows, _ = clean_plan_rows(
        [
            {
                "year": "2025",
                "province": "吉林",
                "type": "一般计划（普通类）",
                "major_type": "物理类",
                "major": "教育学类",
                "major_attr": "普通类",
                "number": "5",
            }
        ]
    )

    assert rows[0]["major_attr"] == "师范类"


def test_merge_duplicate_majors_moves_scores_and_plans():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from collector.nenu_importer import merge_duplicate_majors
    from database.connection import Base
    from database.models import AdmissionScore, EnrollmentPlan, Major

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        target = Major(name="美术学(师范)", display_name="美术学（师范）", major_attr="师范类")
        source = Major(name="美术学(师范)", display_name="美术学（师范）", major_attr="普通类")
        db.add_all([target, source])
        db.flush()
        db.add_all(
            [
                AdmissionScore(
                    year=2025,
                    province="吉林",
                    plan_type="一般计划（普通类）",
                    major_type="物理类",
                    major_id=target.id,
                    min_score=560,
                ),
                AdmissionScore(
                    year=2025,
                    province="吉林",
                    plan_type="一般计划（普通类）",
                    major_type="物理类",
                    major_id=source.id,
                    min_score=560,
                    min_rank=12000,
                ),
                EnrollmentPlan(
                    year=2025,
                    province="吉林",
                    plan_type="一般计划（普通类）",
                    major_type="物理类",
                    major_id=source.id,
                    plan_count=3,
                ),
            ]
        )
        db.flush()

        stats = merge_duplicate_majors(db)
        db.commit()

        assert stats["merged"] == 1
        assert db.get(Major, source.id) is None
        scores = db.query(AdmissionScore).filter_by(major_id=target.id).all()
        assert len(scores) == 1
        assert scores[0].min_rank == 12000
        assert db.query(EnrollmentPlan).filter_by(major_id=target.id).count() == 1
    finally:
        db.close()


def test_plan_tuition_free_is_zero():
    from collector.nenu_importer import clean_plan_rows

    rows, invalid = clean_plan_rows(
        [
            {
                "year": "2025",
                "province": "吉林",
                "type": "一般计划（普通类）",
                "major_type": "物理类",
                "major": "数学与应用数学（公费师范）",
                "major_attr": "师范类",
                "number": "5",
                "edu_len": "四年",
                "edu_cost": "免费",
                "memo": "",
            }
        ]
    )

    assert invalid == 0
    assert rows[0]["tuition"] == 0
    assert rows[0]["tuition_raw"] == "免费"


def test_neu_import_is_idempotent(tmp_path):
    from collector.nenu_importer import import_neu_data
    from database.connection import SessionLocal

    score_csv = tmp_path / "score.csv"
    score_csv.write_text(
        "year,province,type,major_type,major,major_attr,min_admission_score,min_rank,"
        "baseline,min_culture_score,min_major_score,max_culture_score,max_major_score,"
        "max_admission_score,max_rank,avg_culture_score,avg_major_score,avg_admission_score,memo\n"
        "2025,吉林,一般计划（普通类）,物理类,测试专业（中外合作办学）,普通类,500,10000,340,"
        "500,500,510,510,510,9000,505,505,505,\n",
        encoding="utf-8-sig",
    )
    plan_csv = tmp_path / "plan.csv"
    plan_csv.write_text(
        "year,province,type,major_type,major,major_attr,number,edu_len,edu_cost,memo\n"
        "2025,吉林,一般计划（普通类）,物理类,测试专业(中外合作办学),普通类,3,四年,免费,\n",
        encoding="utf-8-sig",
    )

    db = SessionLocal()
    try:
        first = import_neu_data(db, score_csv, plan_csv)
        db.commit()
        second = import_neu_data(db, score_csv, plan_csv)
        db.commit()
    finally:
        db.close()

    assert first["majors"]["inserted"] == 1
    assert first["admission_scores"]["inserted"] == 1
    assert first["enrollment_plans"]["inserted"] == 1
    assert second["majors"]["inserted"] == 0
    assert second["admission_scores"]["inserted"] == 0
    assert second["admission_scores"]["updated"] == 0
    assert second["admission_scores"]["skipped"] == 1
    assert second["enrollment_plans"]["inserted"] == 0
    assert second["enrollment_plans"]["updated"] == 0
