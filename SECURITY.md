# Güvenlik Politikası (Security Policy)

Amazon DPP §5 (CI/CD taramaları) ve §10 (Vulnerability Management) karşılığı.

## Tarama Kadansı
| Tarama | Araç | Ne zaman |
|---|---|---|
| SAST | CodeQL (Python + JS) | her push + PR + haftalık (Pzt 03:00 UTC) + elle |
| Secret scan | gitleaks (tam git geçmişi) | her push + PR + haftalık + elle |
| Backend bağımlılık | pip-audit | her push + PR + haftalık + elle |
| Frontend bağımlılık | yarn/npm audit | her push + PR + haftalık + elle |
| Infra/host vuln scan | *(Actions dışı — hosting sağlayıcısında)* | **≥ ayda 1** (operasyonel) |

Workflow: [`.github/workflows/security.yml`](.github/workflows/security.yml). Sonuçlar
GitHub **Security → Code scanning** sekmesinde + iş **Summary**'sinde görünür.

## Bulgu → Issue → Remediation akışı
1. Tarama bulgu üretir → **Security sekmesi** + iş özeti.
2. gitleaks sır bulgusunda (main push / planlı / elle) **otomatik issue** açılır/güncellenir
   (SLA alanlarıyla). Diğer bulgular manuel olarak
   [Güvenlik Bulgusu template](.github/ISSUE_TEMPLATE/security-finding.md) ile issue'ya dönüştürülür.
3. Owner atanır, **due date** SLA'ya göre belirlenir.
4. Fix uygulanır → **rescan/retest** → issue kapatılır (remediation evidence + retest result).

## Remediation SLA
| Severity | En geç düzeltme |
|---|---|
| **Critical** | 7 takvim günü |
| **High** | 30 takvim günü |
| Medium/Low | planlı backlog |

## Issue kaydı zorunlu alanları
Discovery date · Affected system · Severity · Owner · Due date · Status ·
Remediation evidence · Retest result.

## Release öncesi
Her push (main dahil) SAST + secret + dependency taramasını tetikler. Not: production
deploy `git push → Railway/Cloudflare` ile olduğundan GitHub Actions bir *hard deploy-gate*
değildir; Critical bulgular issue + SLA ile takip edilir ve release öncesi giderilmelidir.

## Sorumlu
Teknik güvenlik sorumlusu: **<doldurulacak>**. Bildirim: **<güvenlik iletişim e-postası>**.
