export default function Takeaways({ items }) {
  if (!items?.length) return null;
  return (
    <ul className="takeaways">
      {items.map((t, i) => (
        <li key={i}>
          <span className="owner">{t.owner || "Team"}</span>
          <div>
            <strong>{t.title}</strong>
            <p>{t.detail}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
