import { getStats } from "../../lib/api";

export default async function Dashboard() {
  const stats = await getStats();
  return <main><h1>Leads: {stats.leads}</h1></main>;
}
