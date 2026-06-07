(() => {
  'use strict';

  const TAB_SESSION_KEY = 'moneybot_tab_session_id';
  const REQUIRED_PROFILE_FIELDS = [
    'primary_goal', 'time_horizon_years', 'risk_tolerance', 'loss_capacity_percent',
    'liquidity_need', 'experience_level', 'account_type',
  ];
  const PROFILE_FIELD_IDS = {
    primary_goal: 'primaryGoal',
    time_horizon_years: 'timeHorizonYears',
    risk_tolerance: 'riskTolerance',
    loss_capacity_percent: 'lossCapacityPercent',
    liquidity_need: 'liquidityNeed',
    experience_level: 'experienceLevel',
    account_type: 'accountType',
    position_size_limit_percent: 'positionSizeLimitPercent',
    sector_limit_percent: 'sectorLimitPercent',
    recommendation_style: 'recommendationStyle',
  };

  let currentProfileImageUrl = null;
  let originalAccount = null;
  let originalInvestorProfile = null;
  let rawSelectedAvatarUrl = null;

  function getTabSessionId() {
    return sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY) || '';
  }

  async function apiFetch(url, options = {}) {
    const headers = Object.assign(
      { 'Content-Type': 'application/json', 'X-Tab-Session-Id': getTabSessionId() },
      options.headers || {},
    );
    const response = await fetch(url, Object.assign({}, options, { headers }));
    if (response.status === 401) {
      location.href = '/login';
      throw new Error('authentication required');
    }
    return response;
  }

  function initials(name) {
    return String(name || '').trim().split(/\s+/).filter(Boolean).slice(0, 2)
      .map((part) => part[0]).join('').toUpperCase() || 'U';
  }

  function renderAvatar(profileImageUrl, name) {
    const image = document.getElementById('avatarImage');
    const initialsNode = document.getElementById('avatarInitials');
    initialsNode.textContent = initials(name);
    if (profileImageUrl) {
      image.src = profileImageUrl;
      image.style.display = 'block';
      initialsNode.style.display = 'none';
    } else {
      image.style.display = 'none';
      initialsNode.style.display = 'grid';
    }
  }

  function setStatus(id, message, type = '') {
    const node = document.getElementById(id);
    node.textContent = message;
    node.className = `save-status${type ? ` ${type}` : ''}`;
  }

  function setBusy(buttonId, busy, busyLabel, idleLabel) {
    const button = document.getElementById(buttonId);
    button.disabled = busy;
    button.textContent = busy ? busyLabel : idleLabel;
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Unable to read selected file.'));
      reader.readAsDataURL(file);
    });
  }

  function applyAvatarTransform() {
    const zoom = document.getElementById('avatarZoom').value;
    const x = document.getElementById('avatarOffsetX').value;
    const y = document.getElementById('avatarOffsetY').value;
    document.getElementById('avatarEditorImage').style.transform =
      `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${zoom})`;
  }

  function openAvatarEditor(dataUrl) {
    rawSelectedAvatarUrl = dataUrl;
    document.getElementById('avatarEditorImage').src = dataUrl;
    document.getElementById('avatarZoom').value = '1.35';
    document.getElementById('avatarOffsetX').value = '0';
    document.getElementById('avatarOffsetY').value = '0';
    applyAvatarTransform();
    document.getElementById('avatarEditorModal').classList.add('open');
  }

  function closeAvatarEditor() {
    document.getElementById('avatarEditorModal').classList.remove('open');
  }

  function buildCroppedAvatarDataUrl() {
    const canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 240;
    const context = canvas.getContext('2d');
    const image = document.getElementById('avatarEditorImage');
    const zoom = Number(document.getElementById('avatarZoom').value || 1);
    const offsetX = Number(document.getElementById('avatarOffsetX').value || 0);
    const offsetY = Number(document.getElementById('avatarOffsetY').value || 0);
    const width = Number(image.naturalWidth || canvas.width);
    const height = Number(image.naturalHeight || canvas.height);
    const fitScale = Math.max(canvas.width / width, canvas.height / height);
    const drawWidth = width * fitScale * zoom;
    const drawHeight = height * fitScale * zoom;
    const x = (canvas.width - drawWidth) / 2 + (offsetX * (canvas.width / 180));
    const y = (canvas.height - drawHeight) / 2 + (offsetY * (canvas.height / 180));
    context.save();
    context.beginPath();
    context.roundRect(0, 0, canvas.width, canvas.height, 72);
    context.clip();
    context.drawImage(image, x, y, drawWidth, drawHeight);
    context.restore();
    return canvas.toDataURL('image/png');
  }

  async function loadAccount() {
    const response = await apiFetch('/api/me');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Unable to load account.');
    const user = payload.user || {};
    originalAccount = {
      name: user.name || '', username: user.username || '', profile_image_url: user.profile_image_url || null,
    };
    currentProfileImageUrl = originalAccount.profile_image_url;
    resetAccountForm();
  }

  function resetAccountForm() {
    if (!originalAccount) return;
    document.getElementById('name').value = originalAccount.name;
    document.getElementById('username').value = originalAccount.username;
    currentProfileImageUrl = originalAccount.profile_image_url;
    document.getElementById('profileImage').value = '';
    renderAvatar(currentProfileImageUrl, originalAccount.name);
    setStatus('accountStatus', '');
  }

  function setInputValue(id, value) {
    document.getElementById(id).value = value === null || value === undefined ? '' : String(value);
  }

  function renderInvestorProfile(profile) {
    originalInvestorProfile = JSON.parse(JSON.stringify(profile));
    Object.entries(PROFILE_FIELD_IDS).forEach(([field, id]) => setInputValue(id, profile[field]));
    setInputValue('excludedSectors', (profile.excluded_sectors || []).join(', '));
    document.getElementById('pennyStocksAllowed').checked = profile.penny_stocks_allowed === true;
    document.getElementById('afterHoursAlerts').checked = profile.after_hours_alerts === true;

    const missingCount = (profile.missing_fields || []).length;
    const completedCount = REQUIRED_PROFILE_FIELDS.length - missingCount;
    const percent = Math.round((completedCount / REQUIRED_PROFILE_FIELDS.length) * 100);
    const pill = document.getElementById('profileStatusPill');
    pill.classList.toggle('complete', Boolean(profile.profile_complete));
    document.getElementById('profileStatusLabel').textContent = profile.profile_complete ? 'Complete' : 'Needs answers';
    document.getElementById('profileProgress').style.width = `${percent}%`;
    document.getElementById('profileVersion').textContent = `Version ${profile.profile_version}`;
    document.getElementById('profileStatusCopy').textContent = profile.profile_complete
      ? 'Your saved answers can now guide suitability and portfolio guardrails.'
      : `${missingCount} required answer${missingCount === 1 ? '' : 's'} remaining. Conservative defaults stay active.`;
    setStatus('investorProfileStatus', '');
  }

  async function loadInvestorProfile() {
    const response = await apiFetch('/api/me/investor-profile');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Unable to load investor profile.');
    renderInvestorProfile(payload.profile);
  }

  function nullableNumber(id) {
    const value = document.getElementById(id).value.trim();
    return value === '' ? null : Number(value);
  }

  function nullableString(id) {
    const value = document.getElementById(id).value.trim();
    return value || null;
  }

  function investorPayload() {
    return {
      profile_version: originalInvestorProfile.profile_version,
      primary_goal: nullableString('primaryGoal'),
      time_horizon_years: nullableNumber('timeHorizonYears'),
      risk_tolerance: nullableString('riskTolerance'),
      loss_capacity_percent: nullableNumber('lossCapacityPercent'),
      liquidity_need: nullableString('liquidityNeed'),
      experience_level: nullableString('experienceLevel'),
      account_type: nullableString('accountType'),
      position_size_limit_percent: nullableNumber('positionSizeLimitPercent'),
      sector_limit_percent: nullableNumber('sectorLimitPercent'),
      excluded_sectors: document.getElementById('excludedSectors').value.split(',').map((item) => item.trim()).filter(Boolean),
      penny_stocks_allowed: document.getElementById('pennyStocksAllowed').checked,
      after_hours_alerts: document.getElementById('afterHoursAlerts').checked,
      recommendation_style: nullableString('recommendationStyle'),
      change_reason: 'Updated investor profile from Account Settings',
    };
  }

  function formatApiError(payload) {
    if (payload.fields && typeof payload.fields === 'object') {
      return Object.values(payload.fields).join(' ');
    }
    return payload.error || 'Unable to save investor profile.';
  }

  document.getElementById('accountForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    setBusy('saveAccountBtn', true, 'Saving…', 'Save account');
    setStatus('accountStatus', 'Saving…');
    try {
      const response = await apiFetch('/api/me/profile', {
        method: 'PUT',
        body: JSON.stringify({
          name: document.getElementById('name').value,
          username: document.getElementById('username').value,
          profile_image_url: currentProfileImageUrl,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Unable to update account.');
      const user = payload.user || {};
      originalAccount = {
        name: user.name || '', username: user.username || '', profile_image_url: user.profile_image_url || null,
      };
      currentProfileImageUrl = originalAccount.profile_image_url;
      renderAvatar(currentProfileImageUrl, originalAccount.name);
      setStatus('accountStatus', 'Account saved.', 'success');
    } catch (error) {
      setStatus('accountStatus', error.message || 'Unable to update account.', 'error');
    } finally {
      setBusy('saveAccountBtn', false, 'Saving…', 'Save account');
    }
  });

  document.getElementById('investorProfileForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!originalInvestorProfile) return;
    setBusy('saveInvestorProfileBtn', true, 'Saving…', 'Save investor profile');
    setStatus('investorProfileStatus', 'Saving profile…');
    try {
      const response = await apiFetch('/api/me/investor-profile', {
        method: 'PUT', body: JSON.stringify(investorPayload()),
      });
      const payload = await response.json();
      if (response.status === 409 && payload.current_profile) {
        renderInvestorProfile(payload.current_profile);
        throw new Error('This profile changed in another tab. Latest values loaded; review and save again.');
      }
      if (!response.ok) throw new Error(formatApiError(payload));
      renderInvestorProfile(payload.profile);
      setStatus('investorProfileStatus', 'Investor profile saved.', 'success');
    } catch (error) {
      setStatus('investorProfileStatus', error.message || 'Unable to save investor profile.', 'error');
    } finally {
      setBusy('saveInvestorProfileBtn', false, 'Saving…', 'Save investor profile');
    }
  });

  document.getElementById('cancelAccountBtn').addEventListener('click', resetAccountForm);
  document.getElementById('resetInvestorProfileBtn').addEventListener('click', () => {
    if (originalInvestorProfile) renderInvestorProfile(originalInvestorProfile);
  });
  document.getElementById('avatarEditBtn').addEventListener('click', () => document.getElementById('profileImage').click());
  document.getElementById('profileImage').addEventListener('change', async (event) => {
    if (!event.target.files[0]) return;
    try { openAvatarEditor(await readFileAsDataUrl(event.target.files[0])); }
    catch (error) { setStatus('accountStatus', error.message, 'error'); }
  });
  ['avatarZoom', 'avatarOffsetX', 'avatarOffsetY'].forEach((id) => {
    document.getElementById(id).addEventListener('input', applyAvatarTransform);
  });
  document.getElementById('cancelAvatarEditBtn').addEventListener('click', closeAvatarEditor);
  document.getElementById('saveAvatarEditBtn').addEventListener('click', () => {
    currentProfileImageUrl = buildCroppedAvatarDataUrl() || rawSelectedAvatarUrl;
    renderAvatar(currentProfileImageUrl, document.getElementById('name').value);
    closeAvatarEditor();
  });

  Promise.all([loadAccount(), loadInvestorProfile()]).catch((error) => {
    setStatus('investorProfileStatus', error.message || 'Unable to load settings.', 'error');
  });
})();
