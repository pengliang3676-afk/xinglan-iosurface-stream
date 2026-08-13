param(
    [Parameter(Mandatory = $true)]
    [string]$TargetFile,

    [Parameter(Mandatory = $true)]
    [string]$ResultFile,

    [switch]$Cycle
)

$ErrorActionPreference = 'Stop'

$nativeSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class XinglanUsbHubPortCycler
{
    private const uint CR_SUCCESS = 0;
    private const uint CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint OPEN_EXISTING = 3;
    private const uint IOCTL_USB_HUB_CYCLE_PORT = 0x00220444;

    private static readonly Guid GuidDevinterfaceUsbHub =
        new Guid("f18a0e88-c30c-11d0-8815-00a0c906bed8");

    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
    private static extern uint CM_Get_Device_Interface_List_SizeW(
        out uint length, ref Guid interfaceClassGuid, string deviceId, uint flags);

    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
    private static extern uint CM_Get_Device_Interface_ListW(
        ref Guid interfaceClassGuid, string deviceId, char[] buffer,
        uint bufferLength, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string fileName, uint desiredAccess, uint shareMode,
        IntPtr securityAttributes, uint creationDisposition,
        uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle device, uint controlCode,
        IntPtr inputBuffer, uint inputBufferSize,
        IntPtr outputBuffer, uint outputBufferSize,
        out uint bytesReturned, IntPtr overlapped);

    public static string GetHubInterfacePath(string hubInstanceId)
    {
        uint length;
        Guid guid = GuidDevinterfaceUsbHub;
        uint result = CM_Get_Device_Interface_List_SizeW(
            out length, ref guid, hubInstanceId,
            CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
        if (result != CR_SUCCESS)
            throw new Win32Exception((int)result,
                "Cannot determine USB hub interface list size");
        if (length <= 1)
            throw new InvalidOperationException(
                "The parent USB hub has no active device interface");

        char[] buffer = new char[length];
        result = CM_Get_Device_Interface_ListW(
            ref guid, hubInstanceId, buffer, length,
            CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
        if (result != CR_SUCCESS)
            throw new Win32Exception((int)result,
                "Cannot read USB hub interface list");

        string path = new string(buffer);
        int terminator = path.IndexOf('\0');
        if (terminator >= 0)
            path = path.Substring(0, terminator);
        if (String.IsNullOrWhiteSpace(path))
            throw new InvalidOperationException(
                "The parent USB hub interface path is empty");
        return path;
    }

    public static uint CyclePort(string hubInterfacePath, uint oneBasedPort)
    {
        if (oneBasedPort == 0)
            throw new ArgumentOutOfRangeException("oneBasedPort");

        using (SafeFileHandle handle = CreateFileW(
            hubInterfacePath, GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero,
            OPEN_EXISTING, 0, IntPtr.Zero))
        {
            if (handle.IsInvalid)
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "Cannot open the parent USB hub");

            IntPtr parameters = Marshal.AllocHGlobal(8);
            try
            {
                Marshal.WriteInt32(parameters, 0, unchecked((int)oneBasedPort));
                Marshal.WriteInt32(parameters, 4, 0);
                uint bytesReturned;
                bool ok = DeviceIoControl(
                    handle, IOCTL_USB_HUB_CYCLE_PORT,
                    parameters, 8, parameters, 8,
                    out bytesReturned, IntPtr.Zero);
                if (!ok)
                    throw new Win32Exception(Marshal.GetLastWin32Error(),
                        "USB hub rejected the port-cycle request");
                return unchecked((uint)Marshal.ReadInt32(parameters, 4));
            }
            finally
            {
                Marshal.FreeHGlobal(parameters);
            }
        }
    }
}
'@

$results = @()
try {
    if (-not ('XinglanUsbHubPortCycler' -as [type])) {
        Add-Type -TypeDefinition $nativeSource -Language CSharp
    }

    $targets = @(Get-Content -LiteralPath $TargetFile -Encoding UTF8 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ } |
        Select-Object -Unique)
    if ($targets.Count -eq 0) {
        throw 'No USB hub target was supplied.'
    }

    foreach ($targetId in $targets) {
        $target = Get-PnpDevice -PresentOnly |
            Where-Object InstanceId -EQ $targetId |
            Select-Object -First 1
        if (-not $target) {
            throw "Target USB hub is not currently present: $targetId"
        }
        if ($target.InstanceId -notlike 'USB\VID_*') {
            throw "Refusing to cycle a non-external USB hub: $targetId"
        }

        $parentId = (Get-PnpDeviceProperty -InstanceId $targetId `
            -KeyName 'DEVPKEY_Device_Parent').Data
        $port = [uint32](Get-PnpDeviceProperty -InstanceId $targetId `
            -KeyName 'DEVPKEY_Device_Address').Data
        $parent = Get-PnpDevice -PresentOnly |
            Where-Object InstanceId -EQ $parentId |
            Select-Object -First 1
        if (-not $parent -or $port -eq 0) {
            throw "Cannot resolve the parent hub port for: $targetId"
        }

        $hubPath = [XinglanUsbHubPortCycler]::GetHubInterfacePath($parentId)
        $status = if ($Cycle) {
            [XinglanUsbHubPortCycler]::CyclePort($hubPath, $port)
        } else {
            [uint32]0
        }
        $results += [pscustomobject]@{
            TargetInstanceId = $targetId
            ParentInstanceId = $parentId
            ConnectionIndex = $port
            StatusReturned = $status
            Cycled = [bool]$Cycle
        }
        if ($Cycle) {
            Start-Sleep -Milliseconds 250
        }
    }

    ConvertTo-Json -InputObject $results -Compress |
        Set-Content -LiteralPath $ResultFile -Encoding UTF8
    exit 0
}
catch {
    [pscustomobject]@{
        Error = $_.Exception.Message
        Results = $results
    } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $ResultFile -Encoding UTF8
    exit 1
}
