// Barrel export for the shared UI building blocks the screens reuse.
export { Button } from "./Button";
export type { ButtonProps, ButtonVariant } from "./Button";
export { Spinner } from "./Spinner";
export type { SpinnerProps } from "./Spinner";
export { ErrorBanner, errorMessage } from "./ErrorBanner";
export type { ErrorBannerProps } from "./ErrorBanner";
export { FormField, TextInput } from "./FormField";
export type { FormFieldProps, TextInputProps } from "./FormField";
export { DataTable } from "./DataTable";
export type { Column, DataTableProps } from "./DataTable";
export { ScreenHeader, SectionHeader } from "./ScreenHeader";
export type { ScreenHeaderProps, SectionHeaderProps } from "./ScreenHeader";
export { StatusBadge } from "./StatusBadge";
export type { StatusBadgeProps } from "./StatusBadge";
export { IdChip } from "./IdChip";
export type { IdChipProps } from "./IdChip";
export { Modal, ConfirmDialog } from "./Modal";
export type { ModalProps, ConfirmDialogProps } from "./Modal";
export { Icon } from "./Icon";
export type { IconName, IconProps } from "./Icon";
export { StatCard, StatCardGrid } from "./StatCard/StatCard";
export type {
  StatCardProps,
  StatCardGridProps,
  StatDelta,
  StatTrend,
} from "./StatCard/StatCard";
export { Skeleton, SkeletonTable } from "./Skeleton";
export type { SkeletonProps, SkeletonVariant, SkeletonTableProps } from "./Skeleton";
export { EmptyState, ErrorState } from "./EmptyState";
export type { EmptyStateProps, ErrorStateProps } from "./EmptyState";
export { ToastProvider, useToast } from "./Toast";
export type { ToastApi, ToastOptions, ToastProviderProps, ToastVariant } from "./Toast";
export {
  DownloadClient,
  detectDesktopOs,
  MAC_INSTALLER_URL,
  WINDOWS_INSTALLER_URL,
} from "./DownloadClient";
export type { DesktopOs, DownloadClientProps } from "./DownloadClient";
