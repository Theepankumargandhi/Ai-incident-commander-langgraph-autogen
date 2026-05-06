import { IncidentConsole } from "@/components/incident-console";

export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ incidentId: string }>;
}) {
  const { incidentId } = await params;
  return <IncidentConsole initialIncidentId={incidentId} detailMode />;
}
