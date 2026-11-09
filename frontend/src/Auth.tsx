import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  apiBase,
  errorText,
  post,
  type AuthResponse,
  type User,
  type Verification,
} from "./api";
import { Brand } from "./ui";

type Pending = Verification & { resendAt: number; expiresAt: number };
type Field = "name" | "email" | "password" | "consent" | "code";
type FieldErrors = Partial<Record<Field, string>>;
const pendingKey = `poputno:verification:${apiBase}`;
const credentialsMessage = "Неверная почта или пароль";
function readPending(): Pending | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    return p &&
      typeof p.verification_id === "string" &&
      typeof p.email === "string" &&
      Number.isFinite(p.resendAt) &&
      Number.isFinite(p.expiresAt)
      ? p
      : null;
  } catch {
    return null;
  }
}
export function Auth({
  onUser,
  onBack,
  initialMessage = "",
}: {
  onUser: (u: User) => void;
  onBack: () => void;
  initialMessage?: string;
}) {
  const [pending, setPending] = useState<Pending | null>(readPending);
  const [mode, setMode] = useState<"login" | "register" | "verify">(() =>
    readPending() ? "verify" : "login",
  );
  const [email, setEmail] = useState(() => readPending()?.email || "");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [consent, setConsent] = useState(false);
  const [code, setCode] = useState<string[]>(Array(6).fill(""));
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState(initialMessage);
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(Date.now());
  const [retryAt, setRetryAt] = useState(0);
  const [resendRetryAt, setResendRetryAt] = useState(0);
  const [needsNewCode, setNeedsNewCode] = useState(false);
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const privacy = useRef<HTMLDialogElement>(null);
  const resendSeconds = Math.max(
    0,
    Math.ceil((Math.max(pending?.resendAt || 0, resendRetryAt) - now) / 1000),
  );
  const waitSeconds = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const expired = !!pending && pending.expiresAt <= now;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [mode]);
  useEffect(() => {
    try {
      if (pending) sessionStorage.setItem(pendingKey, JSON.stringify(pending));
      else sessionStorage.removeItem(pendingKey);
    } catch {
      /* Confirmation remains available in memory. */
    }
  }, [pending]);
  function clearField(field: Field) {
    setErrors((old) => {
      const next = { ...old };
      delete next[field];
      if (
        (field === "email" || field === "password") &&
        old.email === "" &&
        old.password === credentialsMessage
      ) {
        delete next.email;
        delete next.password;
      }
      return next;
    });
    setMessage("");
  }
  function focusError(next: FieldErrors) {
    const field = Object.keys(next)[0];
    if (field)
      form.current
        ?.querySelector<HTMLInputElement>(`[name="${field}"]`)
        ?.focus({ preventScroll: true });
  }
  function switchMode(next: "login" | "register") {
    setMode(next);
    setPending(null);
    setPassword("");
    setVisible(false);
    setCode(Array(6).fill(""));
    setErrors({});
    setMessage("");
    setNotice("");
    setNeedsNewCode(false);
  }
  function accept(result: AuthResponse, fromLogin = false) {
    setErrors({});
    if ("user" in result) {
      setPending(null);
      setPassword("");
      try {
        sessionStorage.removeItem(pendingKey);
      } catch {
        /* optional storage */
      }
      onUser(result.user);
      return;
    }
    setPending({
      ...result,
      resendAt: Date.now() + result.resend_after * 1000,
      expiresAt: Date.now() + result.expires_in * 1000,
    });
    setEmail(result.email);
    setCode(Array(6).fill(""));
    setMode("verify");
    setPassword("");
    setNeedsNewCode(false);
    setNotice(
      fromLogin
        ? "Используйте код из последнего письма или запросите новый."
        : "Проверьте входящие и папку «Спам».",
    );
  }
  function showError(error: unknown, resend = false) {
    const next: FieldErrors = {};
    if (error instanceof ApiError) {
      if (error.retryAfter) {
        const until = Date.now() + error.retryAfter * 1000;
        if (resend) setResendRetryAt(until);
        else setRetryAt(until);
      }
      if (error.code === "invalid_credentials") {
        next.email = "";
        next.password = credentialsMessage;
      } else if (error.code === "email_already_registered")
        next.email = "Эта почта уже зарегистрирована. Войдите в аккаунт.";
      else if (
        [
          "invalid_code",
          "code_expired",
          "verification_attempts_exceeded",
        ].includes(error.code)
      ) {
        next.code =
          error.code === "invalid_code"
            ? "Неверный код. Проверьте цифры из письма."
            : error.code === "code_expired"
              ? "Срок кода истёк. Запросите новый."
              : "Слишком много неверных попыток. Запросите новый код.";
        if (error.code !== "invalid_code") setNeedsNewCode(true);
      } else if (error.fields.length) {
        const labels: Record<string, string> = {
          name: "Укажите имя от 2 до 80 символов.",
          email: "Проверьте адрес электронной почты.",
          password: "Пароль должен содержать от 8 до 128 символов.",
          code: "Введите все шесть цифр из письма.",
        };
        for (const item of error.fields)
          if (item.field in labels)
            next[item.field as Field] = labels[item.field];
      }
    }
    if (Object.keys(next).length) {
      setErrors(next);
      setMessage("");
      focusError(next);
    } else setMessage(errorText(error));
  }
  function validate() {
    const next: FieldErrors = {};
    if (mode === "verify") {
      if (!/^\d{6}$/.test(code.join("")))
        next.code = "Введите все шесть цифр из письма.";
    } else {
      if (
        mode === "register" &&
        (name.trim().length < 2 || name.trim().length > 80)
      )
        next.name = "Укажите имя от 2 до 80 символов.";
      const emailInput = form.current?.elements.namedItem(
        "email",
      ) as HTMLInputElement | null;
      if (!email.trim()) next.email = "Введите электронную почту.";
      else if (emailInput?.validity.typeMismatch || email.length > 254)
        next.email = "Введите адрес в формате name@company.ru.";
      if (!password) next.password = "Введите пароль.";
      else if (password.length < 8 || password.length > 128)
        next.password = "Пароль должен содержать от 8 до 128 символов.";
      if (mode === "register" && !consent)
        next.consent = "Нужно ваше согласие на обработку данных.";
    }
    setErrors(next);
    focusError(next);
    return Object.keys(next).length === 0;
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || waitSeconds > 0) return;
    setMessage("");
    setNotice("");
    if (!validate()) return;
    setBusy(true);
    try {
      if (mode === "verify" && pending)
        accept(
          await post<AuthResponse>("/auth/verify-email", {
            verification_id: pending.verification_id,
            code: code.join(""),
          }),
        );
      else if (mode === "register")
        accept(
          await post<AuthResponse>("/auth/register", {
            name: name.trim(),
            email: email.trim(),
            password,
          }),
        );
      else
        accept(
          await post<AuthResponse>("/auth/login", {
            email: email.trim(),
            password,
          }),
          true,
        );
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }
  async function resend() {
    if (!pending || busy || resendSeconds > 0) return;
    setBusy(true);
    setMessage("");
    setNotice("");
    try {
      accept(
        await post<Verification>("/auth/resend-verification", {
          verification_id: pending.verification_id,
        }),
      );
      setRetryAt(0);
    } catch (error) {
      showError(error, true);
    } finally {
      setBusy(false);
    }
  }
  const title =
    mode === "login"
      ? "Вход"
      : mode === "register"
        ? "Регистрация"
        : "Код из письма";
  const errorHint = (field: Field) => (
    <span id={`auth-${field}-error`} className="field-error" aria-live="polite">
      {errors[field]}
    </span>
  );
  return (
    <div className="page auth-page">
      <header className="app-header auth-header">
        <Brand />
        <button className="text-button" type="button" onClick={onBack}>
          К расчёту
        </button>
      </header>
      <main className="auth-layout">
        <section className="auth-intro">
          <h1>
            Планирование
            <br />
            перевозок
          </h1>
          <p>
            Оцените объём поставок и сравните способы доставки по стоимости и
            срокам.
          </p>
        </section>
        <div className="ticket-shadow auth-card-wrap">
          <form
            ref={form}
            className="auth-ticket perforated"
            data-mode={mode}
            noValidate
            onSubmit={submit}
          >
            <h2 ref={heading} tabIndex={-1}>
              {title}
            </h2>
            <fieldset disabled={busy} className="auth-fields">
              {mode === "verify" ? (
                <>
                  <p className="verify-help">
                    Введите шестизначный код для
                    <br />
                    <strong>{pending?.email}</strong>
                  </p>
                  <div className="code-field">
                    <div
                      className="code-inputs"
                      role="group"
                      aria-label="Шестизначный код из письма"
                      onPaste={(event) => {
                        const digits = event.clipboardData
                          .getData("text")
                          .replace(/\D/g, "")
                          .slice(0, 6);
                        if (digits) {
                          event.preventDefault();
                          clearField("code");
                          setCode(
                            Array.from(
                              { length: 6 },
                              (_, i) => digits[i] || "",
                            ),
                          );
                          inputs.current[Math.min(digits.length, 5)]?.focus();
                        }
                      }}
                    >
                      {code.map((digit, i) => (
                        <input
                          key={i}
                          ref={(el) => {
                            inputs.current[i] = el;
                          }}
                          name="code"
                          aria-label={`Цифра ${i + 1}`}
                          aria-invalid={errors.code !== undefined}
                          aria-describedby="auth-code-error"
                          inputMode="numeric"
                          autoComplete={i === 0 ? "one-time-code" : "off"}
                          pattern="[0-9]"
                          required
                          value={digit}
                          onChange={(event) => {
                            clearField("code");
                            const value = event.target.value.replace(/\D/g, "");
                            if (value.length >= 6) {
                              setCode(value.slice(0, 6).split(""));
                              inputs.current[5]?.focus();
                              return;
                            }
                            setCode((old) =>
                              old.map((v, j) =>
                                j === i ? value.slice(-1) : v,
                              ),
                            );
                            if (value)
                              inputs.current[Math.min(i + 1, 5)]?.focus();
                          }}
                          onFocus={(event) => event.target.select()}
                          onKeyDown={(event) => {
                            if (event.key === "Backspace" && !digit && i > 0)
                              inputs.current[i - 1]?.focus();
                            if (event.key === "ArrowLeft" && i > 0)
                              inputs.current[i - 1]?.focus();
                            if (event.key === "ArrowRight" && i < 5)
                              inputs.current[i + 1]?.focus();
                          }}
                        />
                      ))}
                    </div>
                    {errorHint("code")}
                  </div>
                  {(expired || needsNewCode) && !errors.code && (
                    <p className="code-expired">
                      Запросите новый код, чтобы продолжить.
                    </p>
                  )}
                  <button
                    className="text-button resend"
                    type="button"
                    disabled={busy || resendSeconds > 0}
                    onClick={resend}
                  >
                    {resendSeconds > 0
                      ? `Отправить снова через ${resendSeconds} с`
                      : "Отправить код ещё раз"}
                  </button>
                </>
              ) : (
                <>
                  {mode === "register" && (
                    <div className="auth-field">
                      <label htmlFor="auth-name">Имя</label>
                      <input
                        id="auth-name"
                        name="name"
                        required
                        autoComplete="name"
                        minLength={2}
                        maxLength={80}
                        placeholder="Ваше имя"
                        value={name}
                        aria-invalid={errors.name !== undefined}
                        aria-describedby="auth-name-error"
                        onChange={(e) => {
                          setName(e.target.value);
                          clearField("name");
                        }}
                      />
                      {errorHint("name")}
                    </div>
                  )}
                  <div className="auth-field">
                    <label htmlFor="auth-email">Электронная почта</label>
                    <input
                      id="auth-email"
                      name="email"
                      type="email"
                      autoComplete="email"
                      required
                      maxLength={254}
                      placeholder="name@company.ru"
                      value={email}
                      aria-invalid={errors.email !== undefined}
                      aria-describedby="auth-email-error"
                      onChange={(e) => {
                        setEmail(e.target.value);
                        clearField("email");
                      }}
                    />
                    {errorHint("email")}
                  </div>
                  <div className="auth-field">
                    <label htmlFor="auth-password">Пароль</label>
                    <div className="password-field">
                      <input
                        id="auth-password"
                        name="password"
                        type={visible ? "text" : "password"}
                        required
                        minLength={8}
                        maxLength={128}
                        autoComplete={
                          mode === "register"
                            ? "new-password"
                            : "current-password"
                        }
                        placeholder={
                          mode === "register"
                            ? "Не менее 8 символов"
                            : "Введите пароль"
                        }
                        value={password}
                        aria-invalid={errors.password !== undefined}
                        aria-describedby="auth-password-error"
                        onChange={(e) => {
                          setPassword(e.target.value);
                          clearField("password");
                        }}
                      />
                      <button
                        type="button"
                        aria-label={
                          visible ? "Скрыть пароль" : "Показать пароль"
                        }
                        aria-pressed={visible}
                        onClick={() => setVisible(!visible)}
                      >
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z" />
                          <circle cx="12" cy="12" r="3" />
                          {visible && <path d="m3 3 18 18" />}
                        </svg>
                      </button>
                    </div>
                    {errorHint("password")}
                  </div>
                  {mode === "register" && (
                    <div className="consent-field">
                      <div className="consent">
                        <input
                          id="auth-consent"
                          name="consent"
                          type="checkbox"
                          required
                          checked={consent}
                          aria-invalid={errors.consent !== undefined}
                          aria-describedby="auth-consent-error"
                          onChange={(e) => {
                            setConsent(e.target.checked);
                            clearField("consent");
                          }}
                        />
                        <label htmlFor="auth-consent">
                          Даю согласие на обработку{" "}
                          <button
                            type="button"
                            className="text-button privacy-link"
                            aria-haspopup="dialog"
                            onClick={(event) => {
                              event.preventDefault();
                              privacy.current?.showModal();
                            }}
                          >
                            персональных данных
                          </button>
                        </label>
                      </div>
                      {errorHint("consent")}
                    </div>
                  )}
                </>
              )}
            </fieldset>
            {(message || notice) && (
              <div className="auth-feedback" aria-live="polite">
                {message && (
                  <p role="alert" className="error-text">
                    {message}
                  </p>
                )}
                {notice && <p className="muted">{notice}</p>}
              </div>
            )}
            <button
              className="primary auth-submit"
              disabled={
                busy ||
                waitSeconds > 0 ||
                (mode === "verify" && (expired || needsNewCode))
              }
              type="submit"
            >
              {busy
                ? "Подождите…"
                : waitSeconds > 0
                  ? `Повторить через ${waitSeconds} с`
                  : mode === "verify"
                    ? "Подтвердить почту"
                    : mode === "register"
                      ? "Зарегистрироваться"
                      : "Войти"}
            </button>
            <p className="auth-switch">
              {mode === "verify" ? (
                <button
                  type="button"
                  disabled={busy}
                  className="text-button"
                  onClick={() => switchMode("login")}
                >
                  Изменить почту / вернуться ко входу
                </button>
              ) : (
                <>
                  {mode === "login" ? "Нет аккаунта? " : "Уже есть аккаунт? "}
                  <button
                    className="text-button"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      switchMode(mode === "login" ? "register" : "login")
                    }
                  >
                    {mode === "login" ? "Зарегистрироваться" : "Войти"}
                  </button>
                </>
              )}
            </p>
          </form>
        </div>
      </main>
      <dialog
        ref={privacy}
        className="privacy-dialog"
        aria-labelledby="privacy-title"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            const bounds = event.currentTarget.getBoundingClientRect();
            if (
              event.clientX < bounds.left ||
              event.clientX > bounds.right ||
              event.clientY < bounds.top ||
              event.clientY > bounds.bottom
            )
              privacy.current?.close();
          }
        }}
      >
        <div className="privacy-heading">
          <h2 id="privacy-title">Персональные данные</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="Закрыть информацию о персональных данных"
            onClick={() => privacy.current?.close()}
          >
            ×
          </button>
        </div>
        <p>
          Имя и электронная почта используются для создания аккаунта,
          подтверждения адреса и входа в «Попутно».
        </p>
        <p>
          Пароль хранится на сервере в виде хеша. Открытый пароль в браузере не
          сохраняется.
        </p>
        <p>
          История расчётов хранится в этом браузере отдельно для вашего
          аккаунта. Она не синхронизируется между устройствами.
        </p>
        <button
          className="primary"
          type="button"
          onClick={() => privacy.current?.close()}
        >
          Понятно
        </button>
      </dialog>
    </div>
  );
}
