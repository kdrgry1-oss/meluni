#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
## Fork (2026-06-03) — Favoriler + Ticimax Excel Upload + CAPI Regresyon

backend:
  - task: "Favoriler (Wishlist) API"
    implemented: true
    working: "NA"
    file: "backend/routes/customer.py"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Yeni endpointler: GET /favorites, GET /favorites/ids, POST /favorites/{id}, DELETE /favorites/{id}, POST /favorites/merge. Curl ile manuel test edildi (add/list/remove çalışıyor). require_auth gerektirir. favorites koleksiyonu."
  - task: "Ticimax Excel Ürün Upload"
    implemented: true
    working: "NA"
    file: "backend/routes/integrations.py"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "POST /api/integrations/ticimax/products/upload-excel (multipart file). URUNKARTIID bazında resync. Curl ile create+update test edildi."
  - task: "CAPI Server-Side Tracking Regresyon"
    implemented: true
    working: "NA"
    file: "backend/routes/capi.py, backend/services/capi/*"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Önceki fork'ta testing_agent ile test edilmedi. GET /api/capi/health çalışıyor (capi_active_pixels:1). POST /api/capi/event regresyon testi gerekli."

frontend:
  - task: "Favoriler UI (ProductCard, PDP, Header, Account)"
    implemented: true
    working: "NA"
    file: "frontend/src/context/FavoritesContext.jsx"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "FavoritesProvider eklendi. ProductCard kalp, PDP favori butonu (pdp-favorite-btn), Header sayaçlı kalp (favorites-btn), Account Favorilerim tab grid. Misafir localStorage, login sonrası merge."
  - task: "Ticimax Excel Upload Admin Sayfası"
    implemented: true
    working: "NA"
    file: "frontend/src/pages/admin/TicimaxExcelUpload.jsx"
    priority: "medium"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "/admin/ticimax-excel drag-drop upload + sonuç istatistikleri."

metadata:
  test_sequence: 1

test_plan:
  current_focus:
    - "Favoriler (Wishlist) API"
    - "Favoriler UI (ProductCard, PDP, Header, Account)"
    - "Ticimax Excel Ürün Upload"
    - "CAPI Server-Side Tracking Regresyon"
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "Yeni Favoriler sistemi (backend+frontend), Ticimax Excel upload ve bekleyen CAPI regresyonu test edilmeli. Admin: admin@facette.com/admin123."

## Fork (2026-06-03) #2 — Influencer CRM + Seeding + ROI (Modül 3+4)

backend:
  - task: "Influencer CRM CRUD + Campaigns + ROI"
    implemented: true
    working: "NA"
    file: "backend/routes/influencers.py"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "POST/GET/PUT/DELETE /api/influencers, /api/influencers/{id}/campaigns, /api/influencer-campaigns/{cid} (PUT/DELETE), confirm-share, GET /api/influencers/{id}/roi (aggregation: total_cost vs revenue -> net_profit, ROAS, commission). Default campaign directives '9:16 dikey format' içerir. Curl ile create/campaign/share/roi doğrulandı. Cargo create (/cargo) MNG ayarı yoksa 400 döner (BEKLENEN - mocked). Order linking: orders.create_order influencer_id'yi aff_id (çerez) veya kupon fallback ile bağlar - curl ile kupon fallback doğrulandı (ROI ciro yansıdı)."
  - task: "Attribution aff_id (30 gün çerez)"
    implemented: true
    working: "NA"
    file: "backend/routes/attribution.py, frontend/src/lib/attribution.js"
    priority: "medium"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "attribution.js aff_id'yi URL'den yakalar, facette_aff çerezine (30 gün) yazar, track-visit payload'a ekler. Backend track-visit aff_id'yi session'a kaydeder (first-touch sabit). resolve_attribution_for_order aff_id döndürür."

frontend:
  - task: "Influencer Admin Sayfası"
    implemented: true
    working: "NA"
    file: "frontend/src/pages/admin/Influencers.jsx"
    priority: "medium"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "/admin/influencer (Pazarlama menüsü). Influencer liste/oluştur, kampanya oluştur, ROI kartları, kargo oluştur, paylaşıldı onayı. data-testid: influencers-page, new-influencer-btn, inf-name, inf-coupon, inf-save, new-campaign-btn, camp-title, camp-save, inf-roi."

metadata:
  test_sequence: 2

test_plan:
  current_focus:
    - "Influencer CRM CRUD + Campaigns + ROI"
    - "Influencer Admin Sayfası"
    - "Attribution aff_id (30 gün çerez)"
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "Influencer modülü (Modül 3+4) test edilmeli. MNG kargo/SMS gerçek key gerektirir (mocked) - /cargo endpoint 400 'MNG ayarları yapılmamış' dönerse BEKLENEN davranış. Admin: admin@facette.com/admin123."

## Fork (2026-06-04) #3 — TOTP MFA + Amazon DPP Compliance + Privacy Page

backend:
  - task: "TOTP MFA (2FA)"
    implemented: true
    working: "NA"
    file: "backend/routes/mfa.py, backend/routes/auth.py"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Endpoints: /api/auth/mfa/{status,setup,enable,disable,verify}. Login MFA gate: mfa_enabled ise {mfa_required, mfa_token}. Curl ile full flow test edildi (setup->enable->login challenge->verify->disable). pyotp+qrcode. mfa_secret AES vault'ta. Admin'de MFA KAPALI bırakıldı (normal login bozulmaz)."
  - task: "Compliance / PII Retention / DPP"
    implemented: true
    working: true
    file: "backend/routes/compliance.py, scheduler.py"
    priority: "high"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "Curl ile test edildi: dpp-checklist (14), pii-retention status/config/run, scheduler purge job. Şifre politikası (min12+complexity) personel uçlarına uygulandı, login bozulmadı."

frontend:
  - task: "MFA UI (login challenge + setup card)"
    implemented: true
    working: "NA"
    file: "frontend/src/pages/admin/AdminLogin.jsx, Compliance.jsx, context/AuthContext.jsx"
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "AdminLogin MFA kod adımı (admin-mfa-form, admin-mfa-code-input, admin-mfa-verify-btn). Compliance/DPP sayfasında MfaCard (mfa-setup-btn, mfa-qr, mfa-enable-code, mfa-enable-btn, mfa-disable-btn). AuthContext.verifyMfa eklendi."
  - task: "Gizlilik (Privacy) sayfası /gizlilik"
    implemented: true
    working: true
    file: "frontend/src/pages/GizlilikPolitikasi.jsx"
    priority: "medium"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "Screenshot ile render doğrulandı (Header+Footer+içerik). data-testid privacy-policy-page."

metadata:
  test_sequence: 3

test_plan:
  current_focus:
    - "TOTP MFA (2FA)"
    - "MFA UI (login challenge + setup card)"
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "MFA frontend+integration test edilmeli. ÖNEMLİ: Test sonunda admin'de MFA'yı MUTLAKA DISABLE edin (admin123 normal login kalmalı). TOTP kodları pyotp ile üretilebilir. Admin: admin@facette.com/admin123, /admin/login."
