-- 生产库兜底：已确认记录不可被 UPDATE（模型层之外的第二道防线）。
-- 适用 PostgreSQL/PostGIS；在 migrate 之后手动执行：
--   psql $PGDATABASE -f backend/forest/sql/immutable_confirmed.sql

CREATE OR REPLACE FUNCTION reject_confirmed_update() RETURNS trigger AS $$
BEGIN
    IF OLD.confirmed AND (
        NEW.confirmed IS DISTINCT FROM OLD.confirmed
        OR ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*)
    ) THEN
        RAISE EXCEPTION '% 已确认，禁止修改（已确认调查版不可被静默改变）', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_equationset_immutable ON forest_equationset;
CREATE TRIGGER trg_equationset_immutable
    BEFORE UPDATE ON forest_equationset
    FOR EACH ROW EXECUTE FUNCTION reject_confirmed_update();

DROP TRIGGER IF EXISTS trg_surveyversion_immutable ON forest_surveyversion;
CREATE TRIGGER trg_surveyversion_immutable
    BEFORE UPDATE ON forest_surveyversion
    FOR EACH ROW EXECUTE FUNCTION reject_confirmed_update();
