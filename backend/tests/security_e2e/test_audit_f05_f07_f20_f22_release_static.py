"""Denetim #5, #7, #20, #22: yayın/yedek betiklerinin güvenlik özellikleri (statik denetim)."""
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _codemagic_script():
    y = yaml.safe_load((ROOT / "codemagic.yaml").read_text(encoding="utf-8"))
    wf = y["workflows"]["ios-meluni"]
    return "\n".join(s["script"] for s in wf["scripts"])


def test_f05_build_never_deletes_certificates():
    sc = _codemagic_script()
    assert "certificates delete" not in sc
    assert "CERTIFICATE_PRIVATE_KEY" in sc


def test_f20_locked_install_without_fallback():
    sc = _codemagic_script()
    assert "yarn install --frozen-lockfile" in sc
    assert "|| yarn install" not in sc
    assert "CI=true" in sc and "craco build" in sc


def test_f07_f22_backup_private_encrypted_verified():
    b = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "backup.sh")]).returncode == 0
    assert "R2_BACKUP_BUCKET" in b and '!= "$R2_BUCKET"' in b       # medya bucket'ı yasak
    assert "openssl enc -aes-256-cbc -pbkdf2" in b                    # şifreli
    assert '--uri="$MONGO_URL"' in b and '--db="$DB"' in b            # uygulamanın veritabanı
    assert "head_object" in b and "sha256" in b                       # bulut kopyası doğrulanır
    assert "|| echo" not in b                                         # hata yutulmaz
    assert "mongorestore --quiet --dryRun" in b


def test_f22_weekly_restore_test_into_temp_db():
    r = (ROOT / "deploy" / "restore_test.sh").read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "restore_test.sh")]).returncode == 0
    assert '--nsTo="${TMPDB}.*"' in r and 'TMPDB="restore_test"' in r      # canlı DB'ye geri yüklenmez
    assert "sha256 uyuşmuyor" in r and "drop_database" in r
    inst = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    assert "/etc/cron.weekly/meluni-restore-test" in inst
